"""
Structured logging setup for Meh-Scanner
Uses Python's standard logging with structured context
"""
import logging
import json
from pathlib import Path
from datetime import datetime

# Setup logs directory
LOGS_DIR = Path("logs")
LOGS_DIR.mkdir(exist_ok=True)

# Log file path with date
LOG_FILE = LOGS_DIR / f"meh-scanner-{datetime.now().strftime('%Y-%m-%d')}.log"

class StructuredFormatter(logging.Formatter):
    """Custom formatter that outputs JSON for files and readable text for console"""
    
    def __init__(self, use_json=False):
        super().__init__()
        self.use_json = use_json
    
    def format(self, record):
        # Extract context from the record
        context = getattr(record, 'context', {})
        
        if self.use_json:
            # JSON format for file
            log_entry = {
                'timestamp': datetime.fromtimestamp(record.created).isoformat(),
                'level': record.levelname,
                'logger': record.name,
                'message': record.getMessage(),
                **context
            }
            if record.exc_info:
                log_entry['exception'] = self.formatException(record.exc_info)
            return json.dumps(log_entry)
        else:
            # Human-readable format for console with colors
            timestamp = datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S')
            level_colors = {
                'INFO': '\033[32m',    # Green
                'WARNING': '\033[33m', # Yellow
                'ERROR': '\033[31m',   # Red
                'DEBUG': '\033[36m',   # Cyan
            }
            reset = '\033[0m'
            color = level_colors.get(record.levelname, '')
            
            context_str = ' '.join([f'{k}={v}' for k, v in context.items()])
            context_str = f' {context_str}' if context_str else ''
            
            return f"{color}[{record.levelname}]{reset} {timestamp} {record.getMessage()}{context_str}"

def setup_logging():
    """Configure logging with both console and file handlers"""
    
    # Get root logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    
    # Console handler (human-readable with colors)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = StructuredFormatter(use_json=False)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    # File handler (JSON format)
    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setLevel(logging.INFO)
    file_formatter = StructuredFormatter(use_json=True)
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    
    return logger

# Create global stdlib logger instance
_stdlib_logger = setup_logging()

class StructuredLogger:
    """Helper for structured logging with context"""
    
    @staticmethod
    def _log(level, event, message, **context):
        """Internal log method"""
        extra = {'context': context}
        extra['context']['event'] = event
        getattr(_stdlib_logger, level)(message, extra=extra)
    
    @staticmethod
    def info(event, message, **context):
        StructuredLogger._log('info', event, message, **context)
    
    @staticmethod
    def warning(event, message, **context):
        StructuredLogger._log('warning', event, message, **context)
    
    @staticmethod
    def error(event, message, **context):
        StructuredLogger._log('error', event, message, **context)
    
    @staticmethod
    def debug(event, message, **context):
        StructuredLogger._log('debug', event, message, **context)

# Replace global logger with structured wrapper
logger = StructuredLogger()

def log_run_summary(candidates_found, deals_appended, errors_count, runtime_seconds):
    """Log daily run summary"""
    logger.info(
        "run_completed",
        f"Run completed: {candidates_found} candidates found, {deals_appended} deals appended, {errors_count} errors in {runtime_seconds:.1f}s",
        candidates_found=candidates_found,
        deals_appended=deals_appended,
        errors_count=errors_count,
        runtime_seconds=runtime_seconds
    )

def log_search_start(queries_count):
    """Log search start"""
    logger.info(
        "search_started",
        f"Starting search with {queries_count} queries",
        queries_count=queries_count
    )

def log_search_complete(candidates_found, vibe_threshold):
    """Log search completion"""
    logger.info(
        "search_completed",
        f"Search completed: {candidates_found} candidates found (vibe score >= {vibe_threshold})",
        candidates_found=candidates_found,
        vibe_threshold=vibe_threshold
    )

def log_analysis_start(sites_count):
    """Log analysis start"""
    logger.info(
        "analysis_started",
        f"Starting analysis of {sites_count} sites",
        sites_count=sites_count
    )

def log_analysis_complete(analyses_count, errors_count):
    """Log analysis completion"""
    logger.info(
        "analysis_completed",
        f"Analysis completed: {analyses_count} sites analyzed, {errors_count} errors",
        analyses_count=analyses_count,
        errors_count=errors_count
    )

def log_site_scraped(url, vibe_score, success=True, error=None):
    """Log individual site scrape"""
    if success:
        logger.info(
            "site_scraped",
            f"Scraped site: {url} (vibe_score={vibe_score})",
            url=url,
            vibe_score=vibe_score
        )
    else:
        logger.error(
            "site_scrape_failed",
            f"Failed to scrape site: {url} - {error}",
            url=url,
            error=str(error)
        )

def log_site_analyzed(url, quality_score, success=True, error=None):
    """Log individual site analysis"""
    if success:
        logger.info(
            "site_analyzed",
            f"Analyzed site: {url} (quality_score={quality_score})",
            url=url,
            quality_score=quality_score
        )
    else:
        logger.error(
            "site_analysis_failed",
            f"Failed to analyze site: {url} - {error}",
            url=url,
            error=str(error)
        )

def log_retry_attempt(attempt_number, max_attempts, operation, error=None):
    """Log retry attempt"""
    logger.warning(
        "retry_attempt",
        attempt_number=attempt_number,
        max_attempts=max_attempts,
        operation=operation,
        error=str(error) if error else None,
        message=f"Retry {attempt_number}/{max_attempts} for {operation}" + (f": {error}" if error else "")
    )
