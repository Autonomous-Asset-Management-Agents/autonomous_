import datetime


class MockClock:
    def __init__(self, time_val=1735732800.0):
        self.time_val = time_val

    def now(self):
        return datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)

    def time(self):
        return self.time_val
