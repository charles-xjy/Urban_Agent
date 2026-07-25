"""自定义持久化 checkpointer：SQLite。

langgraph dev 默认的 in-memory checkpointer 在重启后丢失 checkpoint，
导致历史会话点开后内容为空。改用 SQLite 持久化，重启后仍可恢复完整对话。
路径相对本文件，避免受服务进程工作目录影响。
"""

import os

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoints.db")

checkpointer = AsyncSqliteSaver.from_conn_string(DB_PATH)
