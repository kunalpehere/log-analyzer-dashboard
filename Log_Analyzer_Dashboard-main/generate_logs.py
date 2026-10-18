# generate_logs.py
# used to generate a large log file for testing purposes
from datetime import datetime, timedelta
import random
from pathlib import Path
from log_analyzer.runtime import output_directory

levels = ["INFO", "WARNING", "ERROR"]

messages = {
    "INFO": [
        "User logged in",
        "Request processed successfully",
        "Cache refreshed",
        "Database connection established",
        "Scheduled task completed"
    ],
    "WARNING": [
        "Slow query detected taking 5 seconds",
        "Connection pool near limit 90 percent",
        "Disk space for logs below 20 percent",
        "API response delayed",
        "Memory usage above threshold"
    ],
    "ERROR": [
        "Database connection failed",
        "Null pointer exception",
        "Failed to write log file",
        "Authentication service unavailable",
        "Unhandled application exception"
    ]
}

def generate_logs(output_path=None, *, seed=0):
    """Explicit, reproducible fixture generation; never runs at import."""
    output_path = Path(output_path) if output_path is not None else output_directory() / "logs" / "big_test.log"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    start_time = datetime(2024, 3, 10, 9, 0, 0)

    with open(output_path, "w", encoding="utf-8") as f:
        for i in range(10000):
            timestamp = start_time + timedelta(seconds=i * 15)

            level = rng.choices(
                levels,
                weights=[80, 15, 5],  # 80% INFO, 15% WARNING, 5% ERROR
                k=1
            )[0]

            message = rng.choice(messages[level])

            line = (
                f"{timestamp.strftime('%Y-%m-%d')} "
                f"{timestamp.strftime('%H:%M:%S')} "
                f"{level} {message}\n"
            )

            f.write(line)

    print(f"Generated 10,000-line log file: {output_path}")

if __name__ == "__main__":
    generate_logs()
