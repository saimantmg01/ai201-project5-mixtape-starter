# Mixtape Bug Hunt Submission

### Codebase Map

`app.py` is the Flask application factory. It creates the app, configures SQLAlchemy, initializes the shared `db` object, registers the route blueprints, and creates the database tables inside the app context.

`models.py` defines the database schema with SQLAlchemy models. The main entities are `User`, `Song`, `Tag`, `ListeningEvent`, `Rating`, `Playlist`, and `Notification`. It also defines join tables for friendships, song tags, and playlist entries. Most models include a `to_dict()` method so service results can be returned as JSON.

`routes/` contains the HTTP layer. Route functions read request data, call a service function, convert the result to JSON, and translate `ValueError` exceptions into HTTP error responses. The route files are grouped by feature:

- `routes/songs.py`: song search, song detail, rating, and listening actions.
- `routes/playlists.py`: playlist creation, playlist details, playlist songs, and adding songs to playlists.
- `routes/users.py`: user profile, listening streak, notifications, and marking notifications read.
- `routes/feed.py`: friends listening now and friend activity feed endpoints.

`services/` contains the business logic. This is where the open bugs are expected to live.

- `streak_service.py`: records listening events and updates/listens to user streak state.
- `feed_service.py`: builds feed responses from friends' `ListeningEvent` rows.
- `search_service.py`: searches songs by title or artist and returns song dictionaries.
- `notification_service.py`: creates notifications, adds songs to playlists, rates songs, retrieves notifications, and marks notifications read.
- `playlist_service.py`: creates playlists and retrieves playlist metadata or ordered playlist songs.

`tests/` contains focused pytest coverage for the known bug areas: streaks, search, and playlists. These tests are useful both as reproduction tools and as regression checks after fixes.

`seed_data.py` populates a local SQLite database with sample users, songs, tags, playlists, listening events, ratings, notifications, and friendships so the app can be tried manually.

### Data Flow: Listening to a Song and Checking a Streak

Issue #1 is about the listening streak resetting, so I traced the listening/streak flow first.

1. A client sends `POST /songs/<song_id>/listen` with a `user_id`.
2. `routes/songs.py` handles the request in `listen(song_id)`.
3. The route validates that `user_id` is present.
4. The route calls `record_listening_event(user_id, song_id)` from `services/streak_service.py`.
5. `record_listening_event()` loads the `User`.
6. It creates a `ListeningEvent` for the user/song at the current UTC time.
7. It calls `update_listening_streak(user, now)` to update the user's streak fields.
8. It commits the listening event and updated user state.
9. Later, a client sends `GET /users/<user_id>/streak`.
10. `routes/users.py` calls `get_streak(user_id)` from `services/streak_service.py`.
11. `get_streak()` loads the user and returns `user.listening_streak`.

The streak value shown to the user is not calculated fresh from all listening events. It is stored on the `User` row and updated when a listen event is recorded.

### Patterns Noticed

- Routes stay thin: they parse input, call services, and format JSON responses.
- Services own the application behavior and database writes.
- Models define persistence and relationships, but do not contain much business logic beyond serialization.
- Services commonly raise `ValueError` when a requested model does not exist; routes catch that and return `400` or `404`.
- Timestamps are created with `datetime.now(timezone.utc)`, although SQLite may return stored datetimes without timezone info.
- Feed data is derived from `ListeningEvent` rows; there is no separate feed table.
- Playlist order is stored in the `playlist_entries` association table with a `position` column.

### Issue Review and Plan

I read all five issue descriptions before starting bug work:

- Issue #1: a daily listening streak resets on Sunday even though Saturday and Sunday are consecutive days.
- Issue #2: "Friends Listening Now" includes friends who listened the previous night.
- Issue #3: search can return duplicate copies of the same song.
- Issue #4: playlist-add notifications work, but rating notifications are not created.
- Issue #5: playlist song retrieval hides the newest/last song.

Initial plan: start with issue #1 because the user report points to a narrow streak transition, then tackle issue #3 and issue #5 because the existing tests already describe expected behavior for search and playlist ordering. Issue #4 is also a good candidate after comparing the working playlist notification path against the rating path.

### Issue #1 Orientation Notes

Relevant files for issue #1:

- `routes/songs.py`: `listen(song_id)` records a song listen.
- `services/streak_service.py`: `record_listening_event()` and `update_listening_streak()` update the streak.
- `routes/users.py`: `streak(user_id)` returns the saved streak value.
- `tests/test_streaks.py`: includes a Saturday-to-Sunday streak test that matches the user report.

Reproduction target: set a user to have listened on Saturday, then listen again on Sunday. Expected behavior is that the streak increments because only one calendar day passed. The user-reported actual behavior is that the streak resets to `1`.

Focused reproduction command run:

```bash
.venv/bin/python -m pytest tests/test_streaks.py::test_streak_increments_on_sunday -q
```

Result before any fix: the test fails because the streak is `1` after the Sunday listen, but the expected value is `2`.

### AI Assistance and Diagnostic Approach

AI assistance part: I used Claude to help summarize `models.py` and `routes/songs.py`, trace the feed/listening call chain, and use it to think through how to diagnose issue #1 thoroughly.

To diagnose the streak reset issue thoroughly, I did:

1. Start from the user report: a user listens on Saturday, then listens again on Sunday, and the streak should increase instead of resetting.
2. Trace the HTTP flow from `POST /songs/<song_id>/listen` in `routes/songs.py` into `record_listening_event()` in `services/streak_service.py`.
3. Confirm where the streak value is stored. The app stores `listening_streak` and `last_listened_at` on the `User` model, so the bug is likely in the update logic rather than the `GET /users/<user_id>/streak` route.
4. Step through `update_listening_streak()` with concrete dates: Saturday, June 15, 2024 and Sunday, June 16, 2024.
5. Check the intermediate values: `today`, `last_date`, `days_since_last`, and `today.weekday()`.
6. Compare those values against the intended rules in the function docstring: same day should not increment, one day later should increment, and skipped days should reset.
7. Run the focused regression test, `tests/test_streaks.py::test_streak_increments_on_sunday`, before changing code to prove the bug is reproducible.
8. After fixing, rerun the Sunday test and the full streak test file to verify the fix does not break same-day, normal consecutive-day, or skipped-day behavior.

## Root Cause Analysis Entries

### Issue #1: My listening streak keeps resetting

#### How I reproduced it

I reproduced the bug with the existing focused streak test:

```bash
.venv/bin/python -m pytest tests/test_streaks.py::test_streak_increments_on_sunday -q
```

The test creates a user, calls `update_listening_streak()` with Saturday, June 15, 2024, then calls it again with Sunday, June 16, 2024. The expected streak after Sunday is `2` because the listens are on consecutive calendar days. Before the fix, the actual value was `1`, matching the user report that the streak reset on Sunday.

#### How I found the root cause

I traced the flow from the symptom endpoint back into the service code. `GET /users/<user_id>/streak` in `routes/users.py` only reads the stored streak through `get_streak()`, so the bug had to happen earlier when a listen is recorded. `POST /songs/<song_id>/listen` in `routes/songs.py` calls `record_listening_event()` in `services/streak_service.py`, which creates a `ListeningEvent` and then calls `update_listening_streak()`.

Inside `update_listening_streak()`, I compared the documented streak rules with the actual branch conditions. The suspicious line was the consecutive-day branch:

```python
elif days_since_last == 1 and today.weekday() != 6:
```

I knew this was the specific cause because Python's `datetime.weekday()` returns `6` for Sunday, and the failing test used a Sunday date. That meant the code was intentionally skipping the normal consecutive-day increment only on Sundays.

#### The root cause

The streak logic had an extra Sunday condition in the consecutive-day check. The intended rule is: if `days_since_last == 1`, the streak should increment. Instead, the code only incremented when `days_since_last == 1` and the current day was not Sunday.

Because `today.weekday()` returns `6` on Sunday, a Saturday-to-Sunday listen did not enter the increment branch. It fell into the `else` branch and reset `user.listening_streak` to `1`, even though no day was skipped.

#### Fix 

I removed the unnecessary Sunday guard so all consecutive calendar days increment the streak:

```python
elif days_since_last == 1:
    user.listening_streak += 1
```

This fixes the root cause because Sunday is now treated the same as every other consecutive day. I verified the specific failing case and the related streak behaviors:

```bash
.venv/bin/python -m pytest tests/test_streaks.py::test_streak_increments_on_sunday -q
.venv/bin/python -m pytest tests/test_streaks.py -q
```

The focused Sunday test passed, and the full streak test file passed, covering new-user streaks, normal consecutive-day increments, same-day double listens, skipped-day resets, and Saturday-to-Sunday increments.
