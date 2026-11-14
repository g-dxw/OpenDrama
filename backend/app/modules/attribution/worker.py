"""Optional persisted-outbox worker. Disabled unless ATTRIBUTION_WORKER=1.
Run a single batch with python -m app.modules.attribution.worker --once.
"""
import logging
import os
import threading
from sqlalchemy.orm import sessionmaker
from .service import process_due
_log=logging.getLogger(__name__)
_stop=threading.Event()
_thread=None

def run_once():
    from app.db import get_engine
    factory=sessionmaker(bind=get_engine(),expire_on_commit=False)
    with factory() as db:
        try:
            ids=process_due(db);db.commit();return ids
        except Exception:
            db.rollback();raise

def _loop():
    while not _stop.wait(15):
        try: run_once()
        except Exception: _log.warning("Attribution delivery batch failed; persisted events remain queued")

def start_worker():
    global _thread
    if os.environ.get("ATTRIBUTION_WORKER","0")!="1" or (_thread and _thread.is_alive()): return
    _stop.clear();_thread=threading.Thread(target=_loop,name="attribution-outbox",daemon=True);_thread.start()

def stop_worker():
    _stop.set()
    if _thread: _thread.join(timeout=15)

if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument("--once",action="store_true");args=parser.parse_args()
    if args.once: print({"processed":len(run_once())})
    else:
        os.environ["ATTRIBUTION_WORKER"]="1";start_worker()
        try:
            while _thread and _thread.is_alive(): _thread.join(timeout=1)
        except KeyboardInterrupt: stop_worker()
