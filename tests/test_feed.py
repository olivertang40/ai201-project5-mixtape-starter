"""
tests/test_feed.py — Mixtape

Tests for feed_service.py.
Covers Issue #2: Friends Listening Now should only show today's activity,
not events from yesterday.
"""

import pytest
from datetime import datetime, timezone, timedelta
from app import create_app, db
from models import User, Song, ListeningEvent, friendships
from services.feed_service import get_friends_listening_now


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def seed_friends(app):
    """Create a user with one friend and one song."""
    with app.app_context():
        user = User(username="me", email="me@example.com")
        friend = User(username="friend", email="friend@example.com")
        db.session.add_all([user, friend])
        db.session.flush()

        # Make them friends (both directions for the symmetric relationship)
        db.session.execute(
            friendships.insert().values(user_id=user.id, friend_id=friend.id)
        )
        db.session.execute(
            friendships.insert().values(user_id=friend.id, friend_id=user.id)
        )

        song = Song(title="Today's Track", artist="Artist", shared_by=user.id)
        db.session.add(song)
        db.session.commit()

        yield {"user": user, "friend": friend, "song": song}


def test_today_event_appears_in_feed(app, seed_friends):
    """
    A listening event from earlier today should appear in the feed.
    """
    with app.app_context():
        user_id = seed_friends["user"].id
        friend_id = seed_friends["friend"].id
        song_id = seed_friends["song"].id

        # Event from earlier today (a few hours ago)
        today_event_time = datetime.now(timezone.utc).replace(
            hour=1, minute=0, second=0, microsecond=0
        )
        event = ListeningEvent(
            user_id=friend_id, song_id=song_id, listened_at=today_event_time
        )
        db.session.add(event)
        db.session.commit()

        feed = get_friends_listening_now(user_id)
        assert len(feed) == 1
        assert feed[0]["friend"]["username"] == "friend"


def test_yesterday_event_does_not_appear_in_feed(app, seed_friends):
    """
    A listening event from yesterday should NOT appear in Friends Listening Now.
    This is the regression test for Issue #2.
    """
    with app.app_context():
        user_id = seed_friends["user"].id
        friend_id = seed_friends["friend"].id
        song_id = seed_friends["song"].id

        # Event from yesterday
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        event = ListeningEvent(
            user_id=friend_id, song_id=song_id, listened_at=yesterday
        )
        db.session.add(event)
        db.session.commit()

        feed = get_friends_listening_now(user_id)
        assert len(feed) == 0  # Yesterday's event should be filtered out


def test_only_today_shown_when_both_exist(app, seed_friends):
    """
    When a friend has events from both today and yesterday,
    only today's event should appear.
    """
    with app.app_context():
        user_id = seed_friends["user"].id
        friend_id = seed_friends["friend"].id
        song_id = seed_friends["song"].id

        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        today_early = datetime.now(timezone.utc).replace(
            hour=0, minute=30, second=0, microsecond=0
        )

        db.session.add_all([
            ListeningEvent(user_id=friend_id, song_id=song_id, listened_at=yesterday),
            ListeningEvent(user_id=friend_id, song_id=song_id, listened_at=today_early),
        ])
        db.session.commit()

        feed = get_friends_listening_now(user_id)
        # Friend deduplicates to 1 entry, and it should be today's
        assert len(feed) == 1
        assert feed[0]["listened_at"].startswith(today_early.date().isoformat())


def test_no_friends_returns_empty(app, seed_friends):
    """A user with no friends gets an empty feed."""
    with app.app_context():
        # Create a lonely user with no friends
        lonely = User(username="lonely", email="lonely@example.com")
        db.session.add(lonely)
        db.session.commit()

        feed = get_friends_listening_now(lonely.id)
        assert feed == []
