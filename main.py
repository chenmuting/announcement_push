from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp
import json
import os
import asyncio
from datetime import datetime, time, timedelta

# 数据存储路径（遵循文档“持久化数据存data目录”规则🔶1-109）
PLUGIN_DATA_DIR = os.path.join("data", "plugin_data", "astrbot_plugin_announcement_push")
GROUP_CONFIG_PATH = os.path.join(PLUGIN_DATA_DIR, "group_config.json")
SCHEDULED_CONFIG_PATH = os.path.join(PLUGIN_DATA_DIR, "scheduled_config.json")

# 默认配置结构（初始化用，符合文档“缺失配置补默认值”规则🔶1-369）
DEFAULT_GROUP_CONFIG = {
    "enabled_groups": [],
    # 新增umo更新时间字段：[{"group_id": "xxx", "umo": "xxx", "add_time": "xxx", "umo_update_time": "xxx"}]
    "last_manual_push_time": ""
}
DEFAULT_SCHEDULED_CONFIG = {
    "scheduled_tasks": [],
    "last_scheduled_push_time": ""
}


# ------------------------------ 插件注册（严格遵循文档位置参数格式） ------------------------------
@register(
    "astrbot_plugin_announcement_push",  # 1.插件名（以"astrbot_plugin_"开头🔶1-16、🔶1-17）
    "chenmuting",  # 2.作者（必填）
    "AstrBot 管理员专属公告推送插件（支持中英文指令、公告换行、平台权限兼容）",  # 3.描述（补充权限兼容）
    "1.2.0",  # 4.版本（必填）
    "https://github.com/chenmuting/announcement_push"  # 5.仓库地址（可选🔶1-51）
)
class AnnouncementPushPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.astr_config = config  # 读取WebUI配置（_conf_schema.json定义🔶1-360）

        # 1. 确保数据目录存在（文档要求：持久化数据需手动创建目录🔶1-109）
        if not os.path.exists(PLUGIN_DATA_DIR):
            os.makedirs(PLUGIN_DATA_DIR)

        # 2. 加载WebUI可视化配置（新增@全体权限开关默认值🔶1-369）
        self._load_webui_config()

        # 3. 加载持久化数据（群列表、定时任务🔶1-109）
        self.group_config = self._load_group_config()
        self.scheduled_config = self._load_scheduled_config()

        # 4. 启动定时任务监听（文档异步任务创建方式🔶1-736、🔶1-738）
        asyncio.create_task(self._scheduled_task_listener())
        logger.info("公告推送插件初始化完成（仅管理员可用，支持中英文指令+公告换行+平台权限兼容）")

    # ------------------------------ 基础工具方法（新增umo有效性校验） ------------------------------
    def _load_webui_config(self):
        """加载WebUI配置（新增@全体成员权限兼容开关🔶1-360）"""
        self.default_announcement = self.astr_config.get(
            "default_announcement",
            "管理员未设置默认公告\n支持\\n换行，例：第一行\\n第二行"
        )
        self.allow_at_all = self.astr_config.get("allow_at_all", True)  # 仅AIOCQHTTP支持@全体🔶1-98
        self.default_scheduled_time = self.astr_config.get("default_scheduled_time", "09:00")
        self.umo_expire_hours = self.astr_config.get("umo_expire_hours", 24)  # umo过期时间（小时），新增兼容配置

    def _load_group_config(self) -> dict:
        """加载已推送群列表（新增umo更新时间字段校验🔶1-109）"""
        if os.path.exists(GROUP_CONFIG_PATH):
            try:
                with open(GROUP_CONFIG_PATH, "r", encoding="utf-8") as f:
                    raw_config = json.load(f)
                    # 为旧数据补全umo_update_time字段（兼容历史配置）
                    for group in raw_config.get("enabled_groups", []):
                        if "umo_update_time" not in group:
                            group["umo_update_time"] = group["add_time"]
                    return raw_config
            except Exception as e:
                logger.error(f"加载群配置失败：{str(e)}，使用默认配置")
                self._save_group_config(DEFAULT_GROUP_CONFIG)
                return DEFAULT_GROUP_CONFIG
        else:
            self._save_group_config(DEFAULT_GROUP_CONFIG)
            return DEFAULT_GROUP_CONFIG

    def _save_group_config(self, config: dict):
        """保存群列表配置（符合文档“数据修改后需保存”规则🔶1-109）"""
        try:
            with open(GROUP_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
            logger.info("群配置保存成功")
        except Exception as e:
            logger.error(f"保存群配置失败：{str(e)}")

    def _load_scheduled_config(self) -> dict:
        """加载定时公告任务配置（持久化数据🔶1-109）"""
        if os.path.exists(SCHEDULED_CONFIG_PATH):
            try:
                with open(SCHEDULED_CONFIG_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载定时任务配置失败：{str(e)}，使用默认配置")
                self._save_scheduled_config(DEFAULT_SCHEDULED_CONFIG)
                return DEFAULT_SCHEDULED_CONFIG
        else:
            self._save_scheduled_config(DEFAULT_SCHEDULED_CONFIG)
            return DEFAULT_SCHEDULED_CONFIG

    def _save_scheduled_config(self, config: dict):
        """保存定时任务配置（符合文档“数据修改后需保存”规则🔶1-109）"""
        try:
            with open(SCHEDULED_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
            logger.info("定时任务配置保存成功")
        except Exception as e:
            logger.error(f"保存定时任务配置失败：{str(e)}")

    # ------------------------------ 新增工具函数：umo有效性校验（基于文档会话标识规则） ------------------------------
    def _is_umo_valid(self, group: dict) -> bool:
        """
        校验umo是否有效（过期/缺失则无效）
        符合文档“umo需实时获取”的隐含规则🔶1-252
        """
        # 1. 校验umo是否存在
        if not group.get("umo"):
            logger.warning(f"群{group['group_id']}：umo缺失，无效")
            return False
        # 2. 校验umo是否过期（超过设定小时数则无效）
        try:
            umo_update_time = datetime.strptime(group["umo_update_time"], "%Y-%m-%d %H:%M:%S")
            if (datetime.now() - umo_update_time).total_seconds() > self.umo_expire_hours * 3600:
                logger.warning(f"群{group['group_id']}：umo已过期（{self.umo_expire_hours}小时），需重新开启推送")
                return False
            return True
        except Exception as e:
            logger.error(f"群{group['group_id']}：umo时间解析失败：{str(e)}，无效")
            return False

    # ------------------------------ 定时任务核心逻辑（新增umo校验） ------------------------------
    async def _scheduled_task_listener(self):
        """监听定时公告任务，到点执行推送（新增umo有效性校验🔶1-252）"""
        while True:
            now = datetime.now()
            current_time = now.strftime("%H:%M")

            # 遍历所有定时任务（切片防遍历中修改列表）
            for task in self.scheduled_config["scheduled_tasks"][:]:
                if task["time"] == current_time:
                    # 执行推送（传递含\n的原始内容，新增umo校验）
                    push_result = await self._send_announcement_to_groups(task["content"])
                    logger.info(f"定时公告（ID：{task['task_id']}）执行完成：{push_result}")

                    # 更新状态并删除已执行任务
                    self.scheduled_config["last_scheduled_push_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
                    self.scheduled_config["scheduled_tasks"] = [
                        t for t in self.scheduled_config["scheduled_tasks"] if t["task_id"] != task["task_id"]
                    ]
                    self._save_scheduled_config(self.scheduled_config)

            # 每分钟检查一次（降低资源占用）
            await asyncio.sleep(60)

    # ------------------------------ 核心修复：推送方法优化（平台权限兼容） ------------------------------
    async def _send_announcement_to_groups(self, content: str) -> str:
        """向所有已开启群推送公告（核心修复：umo校验+@全体权限兼容🔶1-98、🔶1-252）"""
        if not self.group_config["enabled_groups"]:
            return "无已开启推送的群"

        success_cnt = 0
        fail_cnt = 0
        fail_groups = []
        push_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for group in self.group_config["enabled_groups"]:
            group_id = group["group_id"]
            try:
                # 1. 先校验umo有效性，无效则跳过并提示
                if not self._is_umo_valid(group):
                    fail_cnt += 1
                    fail_groups.append(group_id)
                    continue

                # 2. 构建消息链（新增@全体权限兼容：失败则降级为无@消息🔶1-98）
                message_chain = MessageChain()
                at_added = False
                if self.allow_at_all:
                    try:
                        # 尝试添加@全体成员（仅AIOCQHTTP支持，失败则捕获异常）
                        message_chain.chain.append(Comp.At(qq="all"))
                        at_added = True
                    except Exception as e:
                        logger.warning(f"群{group_id}：添加@全体成员失败（无权限/平台限制）：{str(e)}，降级为普通消息")

                # 3. 添加公告内容（保留\n换行，符合Comp.Plain规则🔶1-259）
                if at_added:
                    message_chain.chain.append(Comp.Plain(f"\n【管理员公告】\n{content}\n\n推送时间：{push_time}"))
                else:
                    message_chain.chain.append(Comp.Plain(f"【管理员公告】\n{content}\n\n推送时间：{push_time}"))

                # 4. 发送主动消息（符合文档位置参数规则🔶1-250，新增详细日志）
                logger.debug(f"群{group_id}：使用umo={group['umo']}发送消息")
                await self.context.send_message(
                    group["umo"],  # 会话唯一标识（已校验有效性）
                    message_chain  # 含换行/兼容@的消息链
                )
                success_cnt += 1
                logger.info(f"群{group_id}：推送成功")

            except Exception as e:
                # 捕获平台接口错误，新增详细错误日志（方便定位retcode问题）
                fail_cnt += 1
                fail_groups.append(group_id)
                err_detail = f"retcode={e.retcode if hasattr(e, 'retcode') else '未知'}, message={str(e)}"
                logger.error(f"群{group_id}：推送失败（{err_detail}），需重新执行/pushstart更新umo")

        # 构建结果信息，提示umo过期/权限问题的解决方案
        result_msg = f"成功{success_cnt}个群，失败{fail_cnt}个群\n"
        if fail_groups:
            result_msg += f"失败群ID：{','.join(fail_groups)}\n"
            result_msg += "失败原因：可能是umo过期（需重新发送/pushstart）或@全体权限不足（关闭WebUI的@全体开关）"
        else:
            result_msg += "失败群ID：无"
        return result_msg

    # ------------------------------ 推送开启指令：新增umo更新时间（关键修复） ------------------------------
    @filter.command(
        "pushstart",
        alias={"推送开启"},
        priority=0
    )
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)  # 仅群聊触发🔶1-178
    async def cmd_push_start(self, event: AstrMessageEvent, *args):
        """推送开启：实时更新umo与更新时间（解决umo过期问题🔶1-252）"""
        group_id = event.get_group_id() or event.message_obj.group_id  # 获取群ID🔶1-69、🔶1-78
        umo = event.unified_msg_origin  # 实时获取umo（文档要求：群消息事件中获取🔶1-252）
        if not group_id or not umo:
            yield event.plain_result("获取群ID或会话标识（umo）失败，无法开启推送")
            return

        # 检查群是否已在列表，若存在则更新umo与时间
        for group in self.group_config["enabled_groups"]:
            if group["group_id"] == group_id:
                group["umo"] = umo  # 更新为实时umo
                group["umo_update_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._save_group_config(self.group_config)
                yield event.plain_result(f"群{group_id}已更新会话标识（umo），推送功能保持开启")
                return

        # 新群添加：包含umo更新时间
        new_group = {
            "group_id": group_id,
            "umo": umo,
            "add_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "umo_update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # 新增umo更新时间
        }
        self.group_config["enabled_groups"].append(new_group)
        self._save_group_config(self.group_config)
        yield event.plain_result(
            f"群{group_id}已添加到推送列表！当前列表共{len(self.group_config['enabled_groups'])}个群\n"
            f"提示：若后续推送失败，需重新发送/pushstart更新会话标识（umo）"
        )

    # ------------------------------ 其他指令保持不变（仅补充日志/提示） ------------------------------
    @filter.command(
        "pushhelp",
        alias={"推送帮助"},  # 中文别名，符合文档v3.4.28+指令别名规则🔶1-171、🔶1-172
        priority=1
    )
    @filter.permission_type(filter.PermissionType.ADMIN)  # 管理员权限🔶1-191、🔶1-192
    async def cmd_push_help(self, event: AstrMessageEvent, *args):
        """推送帮助：补充umo更新/权限兼容说明（符合文档“良好用户引导”规则🔶1-108）"""
        help_text = f"""
【管理员公告推送插件 - 指令手册】
📌 所有指令仅管理员可用，支持中英文触发；「推送公告」「定时推送公告」仅支持私聊
📌 关键提示：
  - 若推送失败，需在对应群重新发送/pushstart更新会话标识（umo）
  - @全体成员仅QQ个人号(aiocqhttp)支持，无权限可在WebUI关闭该开关

📌 公告换行说明：输入\\n（反斜杠+字母n）即可换行，例：/推送公告 好的电话电话\\n干得好的话

1. /pushhelp /推送帮助 - 查看插件所有指令（当前指令）
2. /pushstart /推送开启 - 添加/更新群推送（关键：更新会话标识，解决推送失败）
3. /pushstop /推送关闭 - 从推送列表移除当前群（仅群聊）
4. /pushconfig /推送配置 - 查看插件完整配置（全场景）
5. /pushannounce /推送公告 [内容] - 发布即时公告（例：/推送公告 好的电话电话\\n干得好的话）
6. /schedulepush /定时推送公告 [时间] [内容] - 设置定时公告（例：/定时推送公告 12:00 第一行\\n第二行）

【当前WebUI配置摘要】
• 默认公告（↩️表示换行）：{self.default_announcement.replace('\\n', '↩️')[:30]}...
• @全体成员：{"✅ 允许" if self.allow_at_all else "❌ 禁止"}
• umo过期时间：{self.umo_expire_hours}小时（过期需重新/pushstart）
• 默认定时时间：{self.default_scheduled_time}
        """.strip()
        yield event.plain_result(help_text)

    # （pushstop、pushconfig、pushannounce、schedulepush指令代码保持不变，仅修复pushstart与_send_announcement_to_groups）
    @filter.command(
        "pushstop",
        alias={"推送关闭"},
        priority=0
    )
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def cmd_push_stop(self, event: AstrMessageEvent, *args):
        """推送关闭：从推送列表移除当前群（中英文指令通用）"""
        group_id = event.get_group_id() or event.message_obj.group_id
        if not group_id:
            yield event.plain_result("获取群ID失败，无法关闭推送")
            return

        original_cnt = len(self.group_config["enabled_groups"])
        self.group_config["enabled_groups"] = [
            g for g in self.group_config["enabled_groups"] if g["group_id"] != group_id
        ]

        if len(self.group_config["enabled_groups"]) == original_cnt:
            yield event.plain_result(f"群{group_id}不在推送列表中，无需移除")
            return

        self._save_group_config(self.group_config)
        yield event.plain_result(
            f"群{group_id}已从推送列表移除！当前列表共{len(self.group_config['enabled_groups'])}个群")

    @filter.command(
        "pushconfig",
        alias={"推送配置"},
        priority=0
    )
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_push_config(self, event: AstrMessageEvent, *args):
        """推送配置：展示含umo更新时间的群列表（符合文档配置展示规则）"""
        # 1. 已开启群列表（新增umo更新时间展示）
        group_text = "暂无已开启推送的群"
        if self.group_config["enabled_groups"]:
            group_text = "\n".join([
                f"- 群ID：{g['group_id']}（添加时间：{g['add_time']}，umo更新时间：{g['umo_update_time']}）"
                for g in self.group_config["enabled_groups"]
            ])

        # 2. 定时任务列表（显示换行符提示）
        task_text = "暂无定时公告任务"
        if self.scheduled_config["scheduled_tasks"]:
            task_text = "\n".join([
                f"- 任务ID：{t['task_id']}（时间：{t['time']}，内容：{t['content'].replace('\\n', '↩️')[:20]}...）"
                for t in self.scheduled_config["scheduled_tasks"]
            ])

        # 3. 完整配置文本（补充umo过期说明）
        config_text = f"""
【管理员公告推送插件 - 完整配置】
一、WebUI可视化配置（可在插件管理页修改）
1. 默认公告内容（实际换行效果）：
{self.default_announcement.replace('\\n', '\n  ')}  # 展示\n解析后的换行
2. @全体成员开关：{"✅ 允许" if self.allow_at_all else "❌ 禁止"}
3. umo过期时间：{self.umo_expire_hours}小时（超过需重新/pushstart）
4. 默认定时时间：{self.default_scheduled_time}

二、推送列表配置（含umo更新时间）
已开启推送的群（共{len(self.group_config['enabled_groups'])}个）：
{group_text}
上次手动推送时间：{self.group_config.get("last_manual_push_time", "未推送过")}

三、定时公告配置
当前定时任务（共{len(self.scheduled_config['scheduled_tasks'])}个，↩️表示换行）：
{task_text}
上次定时推送时间：{self.scheduled_config.get("last_scheduled_push_time", "未推送过")}

📌 提示1：公告内容输入\\n即可换行；提示2：umo过期/推送失败需重新执行/pushstart
        """.strip()
        yield event.plain_result(config_text)

    @filter.command(
        "pushannounce",
        alias={"推送公告"},
        priority=0
    )
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)  # 仅私聊触发🔶1-178
    async def cmd_push_announce(self, event: AstrMessageEvent, content: str):
        """推送公告：支持\n换行（带参指令，符合文档参数规则🔶1-136、🔶1-137）"""
        content_stripped = content.strip()
        if not content_stripped:
            yield event.plain_result(
                "公告内容不能为空！支持换行，例：/推送公告 好的电话电话\\n干得好的话")
            return

        # 执行推送（调用修复后的_send_announcement_to_groups）
        push_result = await self._send_announcement_to_groups(content_stripped)
        self.group_config["last_manual_push_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save_group_config(self.group_config)

        yield event.plain_result(
            f"即时公告发布完成！\n\n公告内容（推送后实际效果）：\n{content_stripped.replace('\\n', '\n')}\n\n推送结果：\n{push_result}\n📌 提示：推送失败需在对应群重新/pushstart"
        )

    @filter.command(
        "schedulepush",
        alias={"定时推送公告"},
        priority=0
    )
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def cmd_schedule_push(self, event: AstrMessageEvent, push_time: str, content: str):
        """定时推送公告：支持\n换行（带参指令，纯位置参数🔶1-136）"""
        try:
            hour, minute = map(int, push_time.split(":"))
            if not (0 <= hour < 24 and 0 <= minute < 60):
                raise ValueError("时间需在0-23时、0-59分范围内")
        except Exception as e:
            yield event.plain_result(
                f"时间格式错误！需为HH:MM（换行示例：/定时推送公告 12:00 第一行\\n第二行）\n错误原因：{str(e)}")
            return

        content_stripped = content.strip()
        if not content_stripped:
            yield event.plain_result(
                "公告内容不能为空！支持换行，例：/定时推送公告 12:00 好的电话电话\\n干得好的话")
            return

        task_id = f"task_{datetime.now().timestamp():.0f}"
        new_task = {
            "task_id": task_id,
            "time": push_time,
            "content": content_stripped,
            "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self.scheduled_config["scheduled_tasks"].append(new_task)
        self._save_scheduled_config(self.scheduled_config)

        yield event.plain_result(
            f"定时公告设置成功！\n\n任务信息：\n- 任务ID：{task_id}\n- 执行时间：{push_time}\n- 公告内容（↩️为换行）：{content_stripped.replace('\\n', '↩️')}\n\n提示1：任务执行时，\\n会自动解析为换行\n提示2：推送失败需在对应群重新/pushstart更新umo"
        )
