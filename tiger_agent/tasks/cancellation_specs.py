import asyncio
import inspect

from tiger_agent.tasks.cancellation import RunCancellations


class TestRunCancellations:
    def test_track_registers_and_unregisters(self):
        registry = RunCancellations()

        with registry.track("C1", "1.0") as cancel_requested:
            assert registry.is_tracked("C1", "1.0")
            assert registry.running == 1
            assert not cancel_requested.is_set()

        assert not registry.is_tracked("C1", "1.0")
        assert registry.running == 0

    def test_cancel_sets_the_tracked_event(self):
        registry = RunCancellations()

        with registry.track("C1", "1.0") as cancel_requested:
            assert registry.cancel("C1", "1.0") is True
            assert cancel_requested.is_set()

    def test_cancel_of_an_unknown_key_is_a_noop(self):
        registry = RunCancellations()

        assert registry.cancel("C1", "9.9") is False

        with registry.track("C1", "1.0") as cancel_requested:
            assert registry.cancel("C1", "9.9") is False
            assert registry.cancel("C2", "1.0") is False
            assert not cancel_requested.is_set()

    def test_unregisters_even_when_the_run_raises(self):
        registry = RunCancellations()

        try:
            with registry.track("C1", "1.0"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass

        assert not registry.is_tracked("C1", "1.0")

    def test_concurrent_runs_are_independent(self):
        registry = RunCancellations()

        with (
            registry.track("C1", "1.0") as first,
            registry.track("C1", "2.0") as second,
            registry.track("C2", "1.0") as third,
        ):
            assert registry.running == 3
            assert registry.cancel("C1", "2.0") is True
            assert second.is_set()
            assert not first.is_set()
            assert not third.is_set()

    async def test_five_workers_and_a_listener_on_one_loop(self):
        """Mirror production: workers track, the listener cancels one, all on one loop."""
        registry = RunCancellations()
        cancelled: list[str] = []
        release = asyncio.Event()

        async def worker(ts: str):
            with registry.track("C1", ts) as cancel_requested:
                waiter = asyncio.create_task(cancel_requested.wait())
                releaser = asyncio.create_task(release.wait())
                done, _ = await asyncio.wait(
                    {waiter, releaser}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in (waiter, releaser):
                    task.cancel()
                if waiter in done:
                    cancelled.append(ts)

        workers = [asyncio.create_task(worker(f"{i}.0")) for i in range(5)]
        await asyncio.sleep(0)  # let every worker register
        assert registry.running == 5

        assert registry.cancel("C1", "3.0") is True  # the listener
        release.set()
        await asyncio.gather(*workers)

        assert cancelled == ["3.0"]
        assert registry.running == 0

    def test_registry_methods_never_await(self):
        """The no-lock design relies on track/cancel having no suspension points."""
        assert not inspect.iscoroutinefunction(RunCancellations.cancel)
        assert not inspect.iscoroutinefunction(RunCancellations.track)
        assert not inspect.isasyncgenfunction(RunCancellations.track.__wrapped__)
