"""
Plain-language version: each simulated "user" does what a real person
roughly does — shortens ONE link, then clicks it over and over. Nine
times out of ten they click; one time out of ten they might shorten
something new. This mirrors the "read-heavy, write-light" assumption
every earlier milestone was designed around — this milestone is where
we actually go test whether that assumption held up under real load,
instead of just asserting it in a docstring.

Run with the Locust web UI (recommended for learning — you watch
graphs update live):
    docker compose --profile loadtest up locust
    then open http://localhost:8089

Or headless, for a fixed automated run (useful for a quick benchmark):
    docker compose --profile loadtest run --rm locust \
        -f /mnt/locust/locustfile.py --host http://api:8000 \
        --headless -u 100 -r 10 -t 60s
    (-u 100 = 100 simulated users, -r 10 = ramp up 10/sec, -t 60s = run for 60 seconds)

IMPORTANT: every request uses `catch_response=True` and checks the
status code explicitly, rather than assuming success. Without this, a
failed request (429 rate-limited, 503 database unavailable, whatever)
returns a response body that DOESN'T have the fields we expect — and
blindly doing response.json()["short_code"] on that crashes the entire
simulated user with a raw KeyError, killing that user for the rest of
the test and hiding the REAL error (the actual status code and error
body) behind a confusing traceback instead. Checking explicitly means
Locust records it as a proper, readable failure and the simulated user
keeps going instead of dying.
"""

import uuid

from locust import HttpUser, between, task


class URLShortenerUser(HttpUser):
    # Small random pause between actions, like a real person doesn't
    # click instantly and repeatedly with zero delay.
    wait_time = between(0.1, 0.5)

    def on_start(self):
        """Runs once per simulated user, at the start — like a real
        visitor's first action: shorten a link, then spend the rest of
        the session clicking it."""
        self.short_code = None
        unique_url = f"https://example.com/loadtest/{uuid.uuid4()}"

        with self.client.post(
            "/shorten", json={"url": unique_url}, name="/shorten [setup]", catch_response=True
        ) as response:
            if response.status_code in (200, 201):
                self.short_code = response.json()["short_code"]
                response.success()
            else:
                # This is the line that actually tells us what's
                # wrong, instead of a bare KeyError three layers deep.
                response.failure(
                    f"Setup failed: {response.status_code} - {response.text[:200]}"
                )

    @task(9)
    def redirect(self):
        if self.short_code is None:
            return  # on_start never succeeded for this user — nothing to click

        # name= groups every distinct short code under ONE label in
        # Locust's stats — same cardinality lesson from Milestone 12,
        # applied here too. Without it, Locust's own stats table would
        # grow one row per short code instead of staying at one row
        # for "the redirect endpoint."
        with self.client.get(
            f"/{self.short_code}",
            allow_redirects=False,
            name="/{short_code} [redirect]",
            catch_response=True,
        ) as response:
            # A 301 IS success here — don't let Locust's default
            # "only 2xx is success" assumption mark a correct redirect
            # as a failure.
            if response.status_code == 301:
                response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code} - {response.text[:200]}")

    @task(1)
    def create_new_url(self):
        unique_url = f"https://example.com/loadtest/{uuid.uuid4()}"
        with self.client.post(
            "/shorten", json={"url": unique_url}, name="/shorten", catch_response=True
        ) as response:
            if response.status_code in (200, 201):
                response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code} - {response.text[:200]}")
