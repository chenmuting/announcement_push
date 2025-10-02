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
    "enabled_groups": [],  # 格式：[{"group_id": "xxx", "umo": "xxx", "add_time": "xxx"}]
    "last_manual_push_time": ""
}
DEFAULT_SCHEDULED_CONFIG = {
    "scheduled_tasks": [],  # 格式：[{"task_id": "xxx", "time": "HH:MM", "content": "xxx", "create_time": "xxx"}]
    "last_scheduled_push_time": ""
}


# ------------------------------ 插件注册（严格遵循文档位置参数格式） ------------------------------
@register(
    "astrbot_plugin_announcement_push",  # 1.插件名（以"astrbot_plugin_"开头🔶1-16、🔶1-17）
    "chenmuting",  # 2.作者（必填）
    "AstrBot 管理员专属公告推送插件（支持中英文指令、公告换行、WebUI配置）",  # 3.描述（必填）
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

        # 2. 加载WebUI可视化配置（无配置时用默认值🔶1-369）
        self._load_webui_config()

        # 3. 加载持久化数据（群列表、定时任务🔶1-109）
        self.group_config = self._load_group_config()
        self.scheduled_config = self._load_scheduled_config()

        # 4. 启动定时任务监听（文档异步任务创建方式🔶1-736、🔶1-738）
        asyncio.create_task(self._scheduled_task_listener())
        logger.info("公告推送插件初始化完成（仅管理员可用，支持中英文指令+公告换行）")

    # ------------------------------ 基础工具方法（符合文档配置规则） ------------------------------
    def _load_webui_config(self):
        """加载WebUI配置（_conf_schema.json定义的参数🔶1-360）"""
        self.default_announcement = self.astr_config.get(
            "default_announcement",
            "管理员未设置默认公告\n支持\\n换行，例：第一行\\n第二行"
        )
        self.allow_at_all = self.astr_config.get("allow_at_all", True)  # 仅AIOCQHTTP支持@全体🔶1-98
        self.default_scheduled_time = self.astr_config.get("default_scheduled_time", "09:00")

    def _load_group_config(self) -> dict:
        """加载已推送群列表（持久化数据🔶1-109）"""
        if os.path.exists(GROUP_CONFIG_PATH):
            try:
                with open(GROUP_CONFIG_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
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

    # ------------------------------ 定时任务核心逻辑（文档异步规则） ------------------------------
    async def _scheduled_task_listener(self):
        """监听定时公告任务，到点执行推送（支持换行内容🔶1-259）"""
        while True:
            now = datetime.now()
            current_time = now.strftime("%H:%M")

            # 遍历所有定时任务（切片防遍历中修改列表）
            for task in self.scheduled_config["scheduled_tasks"][:]:
                if task["time"] == current_time:
                    # 执行推送（传递含\n的原始内容）
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

    async def _send_announcement_to_groups(self, content: str) -> str:
        """向所有已开启群推送公告（核心修复：保留\n，让Comp.Plain解析为换行🔶1-86、🔶1-259）"""
        if not self.group_config["enabled_groups"]:
            return "无已开启推送的群"

        success_cnt = 0
        fail_cnt = 0
        fail_groups = []
        push_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for group in self.group_config["enabled_groups"]:
            try:
                # 1. 构建MessageChain（Comp.Plain直接接收含\n的content，不做转义）
                message_chain = MessageChain()
                if self.allow_at_all:
                    message_chain.chain.append(Comp.At(qq="all"))  # @全体成员（仅AIOCQHTTP支持🔶1-98）
                # 2. 关键：保留content中的\n，Comp.Plain会自动解析为实际换行
                message_chain.chain.append(
                    Comp.Plain(f"【管理员公告】\n{content}\n\n推送时间：{push_time}")
                )

                # 3. 发送主动消息（符合文档位置参数规则🔶1-250）
                await self.context.send_message(
                    group["umo"],  # 会话唯一标识
                    message_chain  # 含换行的消息链
                )
                success_cnt += 1
            except Exception as e:
                fail_cnt += 1
                fail_groups.append(group["group_id"])
                logger.error(f"向群{group['group_id']}推送失败：{str(e)}")

        return f"成功{success_cnt}个群，失败{fail_cnt}个群\n失败群ID：{','.join(fail_groups) if fail_groups else '无'}"

    # ------------------------------ 中英文指令（支持换行提示与解析） ------------------------------
    @filter.command(
        "pushhelp",
        alias={"推送帮助"},  # 中文别名，符合文档v3.4.28+指令别名规则🔶1-171、🔶1-172
        priority=1
    )
    @filter.permission_type(filter.PermissionType.ADMIN)  # 管理员权限🔶1-191、🔶1-192
    async def cmd_push_help(self, event: AstrMessageEvent, *args):  # *args兼容@机器人多余参数
        """推送帮助：补充换行使用说明（符合文档“良好用户引导”规则🔶1-108）"""
        help_text = f"""
【管理员公告推送插件 - 指令手册】
📌 所有指令仅管理员可用，支持中英文触发；「推送公告」「定时推送公告」仅支持私聊
📌 公告换行说明：输入\\n（反斜杠+字母n）即可换行，例：/推送公告 好的电话电话\\n干得好的话

1. /pushhelp /推送帮助 - 查看插件所有指令（当前指令）
2. /pushstart /推送开启 - 添加当前群到推送列表（仅群聊）
3. /pushstop /推送关闭 - 从推送列表移除当前群（仅群聊）
4. /pushconfig /推送配置 - 查看插件完整配置（全场景）
5. /pushannounce /推送公告 [内容] - 发布即时公告（例：/推送公告 好的电话电话\\n干得好的话）
6. /schedulepush /定时推送公告 [时间] [内容] - 设置定时公告（例：/定时推送公告 12:00 第一行\\n第二行）

【当前WebUI配置摘要】
• 默认公告（↩️表示换行）：{self.default_announcement.replace('\\n', '↩️')[:30]}...
• @全体成员：{"✅ 允许" if self.allow_at_all else "❌ 禁止"}
• 默认定时时间：{self.default_scheduled_time}
        """.strip()
        yield event.plain_result(help_text)  # 被动消息回复🔶1-246

    @filter.command(
        "pushstart",
        alias={"推送开启"},
        priority=0
    )
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)  # 仅群聊触发🔶1-178
    async def cmd_push_start(self, event: AstrMessageEvent, *args):
        """推送开启：添加当前群到推送列表（中英文指令通用）"""
        group_id = event.get_group_id() or event.message_obj.group_id  # 获取群ID🔶1-69、🔶1-78
        umo = event.unified_msg_origin  # 记录会话标识🔶1-252
        if not group_id:
            yield event.plain_result("获取群ID失败，无法开启推送")
            return

        for group in self.group_config["enabled_groups"]:
            if group["group_id"] == group_id:
                yield event.plain_result(f"群{group_id}已在推送列表中，无需重复添加")
                return

        new_group = {
            "group_id": group_id,
            "umo": umo,
            "add_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self.group_config["enabled_groups"].append(new_group)
        self._save_group_config(self.group_config)
        yield event.plain_result(
            f"群{group_id}已添加到推送列表！当前列表共{len(self.group_config['enabled_groups'])}个群")

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
        """推送配置：展示含换行的公告内容（符合文档配置展示规则）"""
        # 1. 已开启群列表
        group_text = "暂无已开启推送的群"
        if self.group_config["enabled_groups"]:
            group_text = "\n".join([
                f"- 群ID：{g['group_id']}（添加时间：{g['add_time']}）"
                for g in self.group_config["enabled_groups"]
            ])

        # 2. 定时任务列表（显示换行符提示）
        task_text = "暂无定时公告任务"
        if self.scheduled_config["scheduled_tasks"]:
            task_text = "\n".join([
                f"- 任务ID：{t['task_id']}（时间：{t['time']}，内容：{t['content'].replace('\\n', '↩️')[:20]}...）"
                for t in self.scheduled_config["scheduled_tasks"]
            ])

        # 3. 完整配置文本（补充换行说明）
        config_text = f"""
【管理员公告推送插件 - 完整配置】
一、WebUI可视化配置（可在插件管理页修改）
1. 默认公告内容（实际换行效果）：
{self.default_announcement.replace('\\n', '\n  ')}  # 展示\n解析后的换行
2. @全体成员开关：{"✅ 允许" if self.allow_at_all else "❌ 禁止"}
3. 默认定时时间：{self.default_scheduled_time}

二、推送列表配置
已开启推送的群（共{len(self.group_config['enabled_groups'])}个）：
{group_text}
上次手动推送时间：{self.group_config.get("last_manual_push_time", "未推送过")}

三、定时公告配置
当前定时任务（共{len(self.scheduled_config['scheduled_tasks'])}个，↩️表示换行）：
{task_text}
上次定时推送时间：{self.scheduled_config.get("last_scheduled_push_time", "未推送过")}

📌 提示：公告内容输入\\n即可换行，例：“第一行\\n第二行”推送后显示为两行
        """.strip()
        yield event.plain_result(config_text)

    # ------------------------------ 带参指令（支持换行内容解析） ------------------------------
    @filter.command(
        "pushannounce",
        alias={"推送公告"},
        priority=0
    )
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)  # 仅私聊触发🔶1-178
    async def cmd_push_announce(self, event: AstrMessageEvent, content: str):
        """推送公告：支持\n换行（带参指令，符合文档参数规则🔶1-136、🔶1-137）"""
        # 仅去除首尾空白，保留中间的\n（关键：不破坏换行符）
        content_stripped = content.strip()
        if not content_stripped:
            yield event.plain_result(
                "公告内容不能为空！支持换行，例：/推送公告 好的电话电话\\n干得好的话")
            return

        # 执行推送（传递含\n的原始内容）
        push_result = await self._send_announcement_to_groups(content_stripped)
        self.group_config["last_manual_push_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save_group_config(self.group_config)

        # 回复中展示换行预览（让用户确认效果）
        yield event.plain_result(
            f"即时公告发布完成！\n\n公告内容（推送后实际效果）：\n{content_stripped.replace('\\n', '\n')}\n\n推送结果：\n{push_result}\n📌 提示：输入\\n即可实现换行"
        )

    @filter.command(
        "schedulepush",
        alias={"定时推送公告"},
        priority=0
    )
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def cmd_schedule_push(self, event: AstrMessageEvent, push_time: str, content: str):  # 纯位置参数，符合文档🔶1-136
        """定时推送公告：支持\n换行（带参指令，纯位置参数）"""
        # 1. 验证时间格式
        try:
            hour, minute = map(int, push_time.split(":"))
            if not (0 <= hour < 24 and 0 <= minute < 60):
                raise ValueError("时间需在0-23时、0-59分范围内")
        except Exception as e:
            yield event.plain_result(
                f"时间格式错误！需为HH:MM（换行示例：/定时推送公告 12:00 第一行\\n第二行）\n错误原因：{str(e)}")
            return

        # 2. 验证内容（保留\n）
        content_stripped = content.strip()
        if not content_stripped:
            yield event.plain_result(
                "公告内容不能为空！支持换行，例：/定时推送公告 12:00 好的电话电话\\n干得好的话")
            return

        # 3. 创建并保存定时任务（存储含\n的原始内容）
        task_id = f"task_{datetime.now().timestamp():.0f}"
        new_task = {
            "task_id": task_id,
            "time": push_time,
            "content": content_stripped,  # 保留\n，不做转义
            "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self.scheduled_config["scheduled_tasks"].append(new_task)
        self._save_scheduled_config(self.scheduled_config)

        # 回复中标注换行位置
        yield event.plain_result(
            f"定时公告设置成功！\n\n任务信息：\n- 任务ID：{task_id}\n- 执行时间：{push_time}\n- 公告内容（↩️为换行）：{content_stripped.replace('\\n', '↩️')}\n\n提示1：任务执行时，\\n会自动解析为换行\n提示2：例：“第一行\\n第二行”将显示为两行文本"
        )
