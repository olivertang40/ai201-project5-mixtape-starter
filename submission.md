# Project 5: Mixtape Bug Hunt — Submission

## AI Tool Disclosure

I used Claude (an AI assistant) throughout this project for codebase orientation and debugging. Specifically:
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

---

### Issue #1 — Listening streak resets on Sundays

**1. How I reproduced it:**
The README listed this as a Sunday-specific bug. I opened `tests/test_streaks.py` and ran the existing test `test_streak_increments_on_sunday`, which creates a Saturday datetime (`2024-06-15`) and a Sunday datetime (`2024-06-16`), calls `update_listening_streak()` twice, and asserts the streak reaches 2. The test failed — streak was 1 after the Sunday call, confirming the bug was real and reproducible.

**2. How I found the root cause:**
The README pointed to `streak_service.py`. I traced the call chain: `POST /songs/<id>/listen` → `routes/songs.py :: listen()` → `streak_service.record_listening_event()` → `streak_service.update_listening_streak()`. I read `update_listening_streak()` line by line. The three branches are: same day (no change), consecutive day (increment), anything else (reset). I focused on the consecutive-day branch:

```python
elif days_since_last == 1 and today.weekday() != 6:
```

I asked Claude: "what does Python's `weekday()` return for each day of the week?" It confirmed Sunday = 6. That made the bug immediately clear — the condition requires BOTH consecutive AND not-Sunday, so Sunday is always excluded from incrementing regardless of whether the previous day was Saturday.

**3. Root cause:**
`update_listening_streak()` in `streak_service.py` has an extra guard `today.weekday() != 6` on the streak-increment branch. Python's `datetime.weekday()` returns 6 for Sunday. This means the condition `days_since_last == 1 and today.weekday() != 6` evaluates to `False` every Sunday — even when the user listened on Saturday. Execution falls through to the `else` branch and resets the streak to 1. The business rule has no concept of "Sunday doesn't count" — this condition was an incorrect addition with no valid purpose.

**4. The fix and side-effect check:**
Removed `and today.weekday() != 6` from the `elif` branch. The only condition for incrementing should be `days_since_last == 1`.

```python
# Before
elif days_since_last == 1 and today.weekday() != 6:

# After
elif days_since_last == 1:
```

After fixing, I ran the full `tests/test_streaks.py` suite. All 5 tests pass, including the same-day no-change test and the skipped-day reset test, confirming the fix didn't break the other streak boundary conditions.

---

### Issue #2 — Friends Listening Now shows people from yesterday

**1. How I reproduced it:**
The issue says users see friends who listened yesterday, not just today. I wrote a test in `tests/test_feed.py`: created a user with a friend, inserted a `ListeningEvent` with `listened_at` set to yesterday (current time minus 24 hours), then called `get_friends_listening_now()`. The friend appeared in the result even though they listened yesterday. The test `test_yesterday_event_does_not_appear_in_feed` confirmed the failure before any code was changed.

**2. How I found the root cause:**
Traced the call chain: `GET /feed/<user_id>/listening-now` → `routes/feed.py :: listening_now()` → `feed_service.get_friends_listening_now()`. The route does nothing except call the service. I read `get_friends_listening_now()` and immediately spotted:

```python
RECENT_THRESHOLD = timedelta(hours=24)
cutoff = datetime.now(timezone.utc) - RECENT_THRESHOLD
```

The cutoff is "now minus 24 hours" — a rolling window. On Wednesday at 2pm, the cutoff is Tuesday at 2pm, so Tuesday afternoon events are included. The feature is called "Listening **Now**" but the window extends well into yesterday.

**3. Root cause:**
`get_friends_listening_now()` in `feed_service.py` uses `timedelta(hours=24)` as its recency threshold, making the cutoff a rolling 24-hour window rather than the start of today. A 24-hour window is not the same as "today" — any event from the same time yesterday would pass the filter. The fix is to use today's UTC midnight as the cutoff, so only events from the current calendar day are included.

**4. The fix and side-effect check:**
Replaced the rolling `timedelta(hours=24)` window with a cutoff at today's UTC midnight:

```python
# Before
RECENT_THRESHOLD = timedelta(hours=24)
cutoff = datetime.now(timezone.utc) - RECENT_THRESHOLD

# After
now = datetime.now(timezone.utc)
cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
```

After fixing, all 4 tests in `tests/test_feed.py` pass. The `get_activity_feed()` function was not touched — it intentionally has no recency filter and returns the most recent N events regardless of date.

**Regression test:** `tests/test_feed.py` — `test_yesterday_event_does_not_appear_in_feed`

---

### Issue #3 — The same song keeps showing up twice in search

**1. How I reproduced it:**
The issue says duplicate songs appear in search results. I ran `tests/test_search.py`. The test `test_search_no_duplicates_multi_tag_song` creates a song with 3 tags and searches for it — asserting it appears exactly once. It failed with the song appearing 3 times. `test_search_no_duplicates_single_tag_song` passed (1 tag = no duplicate). This confirmed the bug is conditional on how many tags a song has.

**2. How I found the root cause:**
Traced the call chain: `GET /songs/search?q=<query>` → `routes/songs.py :: search()` → `search_service.search_songs()`. I read `search_songs()`:

```python
results = (
    db.session.query(Song)
    .outerjoin(song_tags, Song.id == song_tags.c.song_id)
    .filter(...)
    .all()
)
```

The query does an `outerjoin` with `song_tags`. A JOIN multiplies rows — for each matching row in `song_tags`, the song appears once in the result. A song with 3 tags has 3 rows in `song_tags`, so the join produces 3 copies of that song. A song with no tags gets one NULL row from the outer join, so it appears once. I then checked `Song.to_dict()` in `models.py` and confirmed it already loads tags via `self.tags` (a SQLAlchemy relationship with `lazy="subquery"`). The join in the query is entirely unnecessary.

**3. Root cause:**
`search_songs()` in `search_service.py` performs an `outerjoin` between `Song` and `song_tags`. SQL joins multiply result rows — one row per matching join entry. A song with N tags produces N result rows, causing it to appear N times in the returned list. The join serves no purpose: `Song.to_dict()` already retrieves tags through the SQLAlchemy `Song.tags` relationship, which is loaded automatically. The join was either a leftover from an earlier approach or added by mistake.

**4. The fix and side-effect check:**
Removed the `.outerjoin(song_tags, Song.id == song_tags.c.song_id)` line entirely.

```python
# Before
results = (
    db.session.query(Song)
    .outerjoin(song_tags, Song.id == song_tags.c.song_id)
    .filter(...)
    .all()
)

# After
results = (
    db.session.query(Song)
    .filter(...)
    .all()
)
```

After fixing, all 5 tests in `tests/test_search.py` pass. Songs with 0, 1, and 3 tags all return exactly once. Tags are still correctly included in each song's response via the relationship — confirmed by `test_search_returns_matching_songs` which checks the returned song data.

---

### Issue #4 — No notification when a friend rates your song

**1. How I reproduced it:**
The issue description said users receive a notification when a friend adds their song to a playlist, but not when a friend rates it. I wrote a test in `tests/test_notifications.py`: created a sharer user, a rater user, and a song owned by the sharer; called `rate_song(rater_id, song_id, 5)`; then called `get_notifications(sharer_id)`. The result was an empty list — no notification created. This confirmed the bug before touching any code.

**2. How I found the root cause:**
Traced the call chain: `POST /songs/<id>/rate` → `routes/songs.py :: rate()` → `notification_service.rate_song()`. I read `rate_song()` end to end. It validates the score, fetches the song and user, creates or updates a `Rating` record, commits, and returns the rating. There is no call to `create_notification()` anywhere in the function. I then read `add_to_playlist()` in the same file — it does the same DB work and then calls `create_notification()` for `song.shared_by`. The two functions are architecturally parallel, but `rate_song()` is missing its notification step. The function lives in `notification_service.py` — the fact that it's named and placed there implies it was always intended to send a notification.

**3. Root cause:**
`rate_song()` in `notification_service.py` handles the rating persistence correctly but never calls `create_notification()`. The parallel function `add_to_playlist()` in the same file does call `create_notification()` after its DB work. This is an architectural omission — the notification step was simply never added to `rate_song()`. No logic is wrong; the step is entirely absent.

**4. The fix and side-effect check:**
Added a `create_notification()` call after `db.session.commit()`, mirroring the pattern in `add_to_playlist()`. Added a self-notification guard (`if song.shared_by != user_id`) so a user rating their own song doesn't generate a notification.

```python
# Added after db.session.commit()
if song.shared_by != user_id:
    create_notification(
        user_id=song.shared_by,
        notification_type="song_rated",
        body=f"{rater.username} rated your song '{song.title}'.",
    )
```

After fixing, I verified: (a) `rate_song()` still returns the correct `Rating` object, (b) updating an existing rating still works, (c) self-rating produces no notification. All 4 tests in `tests/test_notifications.py` pass. The `add_to_playlist()` path was not touched and its tests still pass.

**Regression test:** `tests/test_notifications.py`

---

### Issue #5 — Last song in a playlist never shows up

**1. How I reproduced it:**
The issue description said the last song in any playlist is missing from the response. I ran `tests/test_playlists.py`. The test `test_playlist_returns_all_songs` creates a 5-song playlist and asserts `len(songs) == 5`. It failed with `assert 4 == 5`. The test `test_playlist_returns_songs_in_order` also failed — the returned list stopped at Track 4. This confirmed the bug: exactly one song is always missing, and it's always the last one.

**2. How I found the root cause:**
Traced the call chain: `GET /playlists/<id>/songs` → `routes/playlists.py :: get_songs()` → `playlist_service.get_playlist_songs()`. I read `get_playlist_songs()`. The SQL query joins `playlist_entries`, filters by playlist ID, and orders by `position` ascending — all correct. Then I read the return statement:

```python
return [song.to_dict() for song in songs[:-1]]
```

`songs[:-1]` is Python slice notation for "all elements except the last." The query was fetching all 5 songs correctly; the slice was discarding the last one before returning. No ambiguity — this is the exact line causing the symptom.

**3. Root cause:**
`get_playlist_songs()` in `playlist_service.py` applies `[:-1]` slice to the query results before returning them. In Python, `list[:-1]` returns all elements up to but not including the last. The SQL query retrieves all songs correctly, but the return statement unconditionally strips the final entry. This affects every playlist regardless of size — a 1-song playlist returns empty, a 5-song playlist returns 4.

**4. The fix and side-effect check:**
Removed `[:-1]` from the list comprehension.

```python
# Before
return [song.to_dict() for song in songs[:-1]]

# After
return [song.to_dict() for song in songs]
```

After fixing, all 3 tests in `tests/test_playlists.py` pass, including `test_empty_playlist_returns_empty_list` (confirms empty playlists still return `[]` correctly, not an error).

---

## Regression Tests

**Issue #2** — `tests/test_feed.py`:
- `test_yesterday_event_does_not_appear_in_feed` — core regression: yesterday's listening event must not appear in Friends Listening Now
- `test_today_event_appears_in_feed` — today's event should appear
- `test_only_today_shown_when_both_exist` — when both exist, only today's is returned
- `test_no_friends_returns_empty` — no friends returns empty list

**Issue #4** — `tests/test_notifications.py`:
- `test_rating_creates_notification_for_sharer` — core regression: notification created for sharer when someone else rates their song
- `test_rating_notification_not_sent_to_self` — no notification when rating your own song
- `test_rating_returns_rating_object` — Rating object still returned correctly after fix
- `test_updating_rating_does_not_duplicate_notification` — update-rating behavior confirmed
