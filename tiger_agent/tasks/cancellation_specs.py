import asyncio
import inspect

from tiger_agent.tasks.cancellation import RunCancellations


class TestRunCancellations:
    def test_track_registers_and_unregisters(self):
        registry = RunCancellations()

        with registry.track("C1", "1.0") as run:
            assert registry.is_tracked("C1", "1.0")
            assert registry.running == 1
            assert not run.cancel_requested.is_set()

        assert not registry.is_tracked("C1", "1.0")
        assert registry.running == 0

    def test_cancel_sets_the_tracked_event(self):
        registry = RunCancellations()

        with registry.track("C1", "1.0") as run:
            assert registry.cancel("C1", "1.0") is True
            assert run.cancel_requested.is_set()

    def test_cancel_of_an_unknown_key_is_a_noop(self):
        registry = RunCancellations()

        assert registry.cancel("C1", "9.9") is False

        with registry.track("C1", "1.0") as run:
            assert registry.cancel("C1", "9.9") is False
            assert registry.cancel("C2", "1.0") is False
            assert not run.cancel_requested.is_set()

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
            assert second.cancel_requested.is_set()
            assert not first.cancel_requested.is_set()
            assert not third.cancel_requested.is_set()

    async def test_five_workers_and_a_listener_on_one_loop(self):
        """Mirror production: workers track, the listener cancels one, all on one loop."""
        registry = RunCancellations()
        cancelled: list[str] = []
        release = asyncio.Event()

        async def worker(ts: str):
            with registry.track("C1", ts) as run:
                waiter = asyncio.create_task(run.cancel_requested.wait())
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


class TestCancelForUser:
    def test_cancels_only_that_users_runs_in_that_thread(self):
        registry = RunCancellations()

        with (
            registry.track("C1", "1.0", user="U_A", thread_ts=None) as root,
            registry.track("C1", "2.0", user="U_A", thread_ts="1.0") as follow_up,
            registry.track("C1", "3.0", user="U_B", thread_ts="1.0") as someone_else,
            registry.track("C1", "4.0", user="U_A", thread_ts="9.0") as other_thread,
            registry.track("C2", "1.0", user="U_A", thread_ts=None) as other_channel,
        ):
            cancelled = registry.cancel_for_user(
                channel="C1", thread="1.0", user="U_A", except_ts="5.0"
            )

            assert cancelled == 2
            assert root.cancel_requested.is_set()
            assert follow_up.cancel_requested.is_set()
            assert root.reason == "user_request"
            assert not someone_else.cancel_requested.is_set()
            assert not other_thread.cancel_requested.is_set()
            assert not other_channel.cancel_requested.is_set()

    def test_the_asking_run_is_never_cancelled(self):
        registry = RunCancellations()

        with (
            registry.track("C1", "1.0", user="U_A", thread_ts=None) as earlier,
            registry.track("C1", "2.0", user="U_A", thread_ts="1.0") as asking,
        ):
            cancelled = registry.cancel_for_user(
                channel="C1", thread="1.0", user="U_A", except_ts="2.0"
            )

            assert cancelled == 1
            assert earlier.cancel_requested.is_set()
            assert not asking.cancel_requested.is_set()

    def test_nothing_to_cancel_returns_zero(self):
        registry = RunCancellations()

        with registry.track("C1", "2.0", user="U_A", thread_ts="1.0"):
            assert (
                registry.cancel_for_user(
                    channel="C1", thread="1.0", user="U_A", except_ts="2.0"
                )
                == 0
            )

    def test_already_cancelled_runs_are_not_counted_twice(self):
        registry = RunCancellations()

        with registry.track("C1", "1.0", user="U_A", thread_ts=None) as run:
            assert registry.cancel("C1", "1.0") is True
            assert run.reason == "message_deleted"

            assert registry.cancel_for_user(channel="C1", thread="1.0", user="U_A") == 0
            # the first reason sticks
            assert run.reason == "message_deleted"
