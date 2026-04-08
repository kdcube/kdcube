import logging
from abc import ABC, abstractmethod
from kdcube_ai_app.apps.metrics.rest.events.event_type import EventBase, ExternalLogEvent
import kdcube_ai_app.apps.utils.logging_config as logging_config

logging_config.configure_logging()
events_logger = logging.getLogger("kdcube.events")


class ExternalLogCollector(ABC):
    @abstractmethod
    def process(self, data: EventBase) -> None:
        ...


class LogCollectorService(ExternalLogCollector):
    def process(self, data: EventBase) -> None:
        try:
            if isinstance(data, ExternalLogEvent):
                event_json = data.model_dump_json()

                log_level = data.level.upper()
                if log_level == "WARN":
                    log_level = "WARNING"

                level_int = getattr(logging, log_level, logging.INFO)
                events_logger.log(level_int, event_json)

        except Exception as e:
            events_logger.error(f"Error processing event: {e}", exc_info=True)
            raise