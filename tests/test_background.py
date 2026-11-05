"""run_in_background debe funcionar desde un hilo sin event loop (como las rutas síncronas de FastAPI)."""
import threading

from app.services.background import run_in_background


def test_runs_coroutine_from_thread_without_event_loop():
    done = threading.Event()
    seen: list[str] = []

    async def job(value: str) -> None:
        seen.append(value)
        done.set()

    worker = threading.Thread(target=lambda: run_in_background(job, "enviado"))
    worker.start()
    worker.join()
    assert done.wait(5)
    assert seen == ["enviado"]


def test_errors_are_logged_not_raised(caplog):
    done = threading.Event()

    async def boom() -> None:
        done.set()
        raise RuntimeError("smtp caído")

    run_in_background(boom)
    assert done.wait(5)
