# Project 5: Mixtape Bug Hunt — Submission

## AI Tool Disclosure

I used Kiro (an AI-powered IDE) throughout this project for codebase orientation and debugging. Specifically:
- Asked it to explain each service file's responsibility and trace call chains
- Used it to walk through the execution path from route → service for each bug
- Verified all root cause analysis independently by reading the code myself before confirming any fix

---

## Codebase Map

### Main Files and Their Roles

**`app.py`**
Flask app factory (`create_app()`). Initializes SQLAlchemy (`db`), registers all route blueprints, and handles test config overrides (e.g., in-memory SQLite for tests).

**`models.py`**
Defines 6 SQLAlchemy models:
- `User` — username, email, listening streak, last listened timestamp
- `Song` — title, artist, album, genre, who shared it and when, optional share note
- `Tag` — simple label (name only)
- `Rating` — a user's 1–5 score for a song; unique per (user, song) pair
- `ListeningEvent` — records when a user listened to a song
- `Playlist` — name, creator, collaborative flag
- `Notification` — inbox message for a user (type + body + read flag)

Also defines three association tables:
- `friendships` — symmetric many-to-many between Users
- `song_tags` — many-to-many between Song and Tag
- `playlist_entries` — many-to-many between Playlist and Song, with `position`, `added_by`, and `added_at` columns

**`routes/`** — Input parsing and HTTP response formatting only. Every route immediately delegates to a service function. No business logic lives here.
- `songs.py` — search, get detail, rate, listen
- `playlists.py` — create, get detail, get songs, add song
- `users.py` — profile, streak, notifications, mark read
- `feed.py` — friends listening now, activity feed

**`services/`** — All business logic lives here.
- `streak_service.py` — records listening events and updates consecutive-day streaks
- `feed_service.py` — returns friends' recent listening activity
- `search_service.py` — searches songs by title or artist
- `notification_service.py` — creates notifications; handles rating and playlist-add actions
- `playlist_service.py` — creates playlists and retrieves ordered song lists

**`seed_data.py`**
Populates the database with 5 users, 13 songs, 3 playlists, and 10 tags for local development and manual testing.

**`tests/`**
Pytest test suite using in-memory SQLite. Each test file covers one service. Fixtures create isolated test data per test.

---

### Data Flow: User Rates a Song → Notification Sent to Sharer

```
POST /songs/<song_id>/rate
  └── routes/songs.py :: rate()
        - parses user_id and score from JSON body
        - validates required fields
        └── notification_service.rate_song(user_id, song_id, score)
              - validates score is 1–5
              - fetches Song and User from DB
              - creates or updates Rating record
              - if rater != sharer: calls create_notification(song.shared_by, "song_rated", ...)
              - returns Rating instance
        - returns rating.to_dict() with HTTP 201
```

### Pattern I Noticed

Every route delegates immediately to a service. Routes handle: JSON parsing, required-field validation, try/except for ValueError → 400/404. Services handle: all DB queries, business rules, and side effects (notifications, streak updates). This separation is consistent across all five route files.

---

## Bug Fixes

### Issue #1 — Listening streak resets on Sundays

**File:** `services/streak_service.py`

**How I reproduced it:**
Called `update_listening_streak()` directly in a test with a Saturday datetime followed by a Sunday datetime. The streak reset to 1 instead of incrementing to 2. The test `test_streak_increments_on_sunday` in `tests/test_streaks.py` confirmed the failure.

**Root Cause Analysis:**

| Field | Detail |
|-------|--------|
| **What was wrong** | The streak increment condition included an extra check `today.weekday() != 6`, which prevented incrementing on Sundays |
| **Why it was wrong** | `weekday() == 6` is Sunday in Python. The condition `days_since_last == 1 and today.weekday() != 6` means "consecutive day AND not Sunday" — so a valid Saturday→Sunday streak always fell through to the `else` branch and reset to 1 |
| **Where the bug was** | `streak_service.py`, `update_listening_streak()`, the `elif` branch |
| **How I fixed it** | Removed `and today.weekday() != 6` — the only condition needed to increment is `days_since_last == 1` |
| **What the fix looks like** | `elif days_since_last == 1:` (was: `elif days_since_last == 1 and today.weekday() != 6:`) |

---

### Issue #4 — No notification when a friend rates your song

**File:** `services/notification_service.py`

**How I reproduced it:**
Called `rate_song()` with a rater user ID and a song shared by a different user, then called `get_notifications()` for the sharer. The notification list was empty. The test `test_rating_creates_notification_for_sharer` in `tests/test_notifications.py` confirmed the failure.

**Root Cause Analysis:**

| Field | Detail |
|-------|--------|
| **What was wrong** | `rate_song()` saved the rating but never called `create_notification()` |
| **Why it was wrong** | The function is in `notification_service.py` alongside `add_to_playlist()`, which does call `create_notification()` after its operation. `rate_song()` was missing the parallel notification step — an architectural omission, not a typo |
| **Where the bug was** | `notification_service.py`, `rate_song()`, after `db.session.commit()` |
| **How I fixed it** | Added a `create_notification()` call for `song.shared_by` with type `"song_rated"`, guarded by `if song.shared_by != user_id` to avoid self-notifications |
| **Regression test** | `tests/test_notifications.py` — covers notification sent, no self-notification, rating still returned correctly, and update-rating behavior |

---

### Issue #5 — Last song in a playlist never shows up

**File:** `services/playlist_service.py`

**How I reproduced it:**
Created a playlist with 5 songs using the test fixture in `tests/test_playlists.py` and called `get_playlist_songs()`. It returned 4 songs. Track 5 was always missing regardless of what song was last. The test `test_playlist_returns_all_songs` confirmed the failure.

**Root Cause Analysis:**

| Field | Detail |
|-------|--------|
| **What was wrong** | The return statement used `songs[:-1]` instead of `songs` |
| **Why it was wrong** | `songs[:-1]` is Python slice notation for "all elements except the last one" — so the final song in the ordered list was always dropped from the response |
| **Where the bug was** | `playlist_service.py`, `get_playlist_songs()`, the return statement |
| **How I fixed it** | Changed `return [song.to_dict() for song in songs[:-1]]` to `return [song.to_dict() for song in songs]` |
| **What the fix looks like** | One character change: removed `[:-1]` from the list comprehension |

---

## Regression Tests

Written for **Issue #4** in `tests/test_notifications.py`:

- `test_rating_creates_notification_for_sharer` — core regression: confirms a notification is created for the sharer when someone else rates their song
- `test_rating_notification_not_sent_to_self` — confirms no notification when a user rates their own song
- `test_rating_returns_rating_object` — confirms the Rating object is still returned correctly after the fix
- `test_updating_rating_does_not_duplicate_notification` — confirms behavior when updating an existing rating
