"""Reset an accidentally started collection timer only when no DVIR samples exist."""
from dvir_learning.settings import db_path
from dvir_learning.store import connect, get_state

path = db_path()
conn = connect(path)
try:
    count = int(conn.execute("SELECT COUNT(*) FROM dvir_samples").fetchone()[0])
    started = get_state(conn, "collection_started_at")
    if count != 0:
        raise SystemExit(
            f"Refusing to reset: {count} sample(s) exist in {path}. Preserve the current collection history."
        )
    conn.execute("DELETE FROM pipeline_state WHERE state_key='collection_started_at'")
    conn.commit()
    print(f"Confirmed zero DVIR samples. Removed stale collection timer from {path}.")
    print("The next successful capture will start the 72-hour window.")
finally:
    conn.close()
