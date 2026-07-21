"""真实账户每日估值快照调度器。"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.core.portfolio_ledger import capture_snapshot_job

logger = logging.getLogger(__name__)


class PortfolioSnapshotScheduler:
    """每天北京时间 08:05 保存一次组合估值，覆盖美股前一交易日收盘。"""

    def __init__(self, timezone: str = "Asia/Shanghai"):
        self.timezone = timezone
        self.scheduler = AsyncIOScheduler(timezone=timezone)

    async def _capture(self) -> None:
        await asyncio.to_thread(capture_snapshot_job, "scheduled")

    def start(self) -> None:
        self.scheduler.add_job(
            self._capture,
            trigger=CronTrigger(hour=8, minute=5, timezone=self.timezone),
            id="portfolio_daily_valuation",
            name="真实账户每日估值",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.start()
        logger.info("真实账户估值调度器已启动")

        # 首次部署立即建立收益统计基线，不阻塞应用启动与健康检查。
        self.scheduler.add_job(
            self._capture,
            id="portfolio_initial_valuation",
            name="真实账户初始估值",
            replace_existing=True,
        )

    def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)
        logger.info("真实账户估值调度器已关闭")
