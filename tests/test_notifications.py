"""
tests/test_notifications.py — Mixtape

Tests for notification logic in notification_service.py.
Covers Issue #4: rating a song should notify the original sharer.
"""

import pytest
from app import create_app, db
from models import User, Song, Notification
from services.notification_service import rate_song, get_notifications


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def seed_users_and_song(app):
    """Create two users and a song shared by one of them."""
    with app.app_context():
        sharer = User(username="sharer", email="sharer@example.com")
        rater = User(username="rater", email="rater@example.com")
        db.session.add_all([sharer, rater])
        db.session.flush()

        song = Song(
            title="Test Song",
            artist="Test Artist",
            shared_by=sharer.id,
        )
        db.session.add(song)
        db.session.commit()

        yield {"sharer": sharer, "rater": rater, "song": song}


def test_rating_creates_notification_for_sharer(app, seed_users_and_song):
    """
    When a user rates a song, the original sharer should receive a notification.
    This is the regression test for Issue #4.
    """
    with app.app_context():
        sharer_id = seed_users_and_song["sharer"].id
        rater_id = seed_users_and_song["rater"].id
        song_id = seed_users_and_song["song"].id

        rate_song(rater_id, song_id, 5)

        notifications = get_notifications(sharer_id)
        assert len(notifications) == 1
        assert notifications[0]["type"] == "song_rated"
        assert "rater" in notifications[0]["body"]
        assert "Test Song" in notifications[0]["body"]


def test_rating_notification_not_sent_to_self(app, seed_users_and_song):
    """
    When a user rates their own song, no notification should be created.
    """
    with app.app_context():
        sharer_id = seed_users_and_song["sharer"].id
        song_id = seed_users_and_song["song"].id

        rate_song(sharer_id, song_id, 4)

        notifications = get_notifications(sharer_id)
        assert len(notifications) == 0


def test_rating_returns_rating_object(app, seed_users_and_song):
    """rate_song should return the Rating instance regardless of notification."""
    with app.app_context():
        rater_id = seed_users_and_song["rater"].id
        song_id = seed_users_and_song["song"].id

        rating = rate_song(rater_id, song_id, 3)
        assert rating.score == 3
        assert rating.user_id == rater_id
        assert rating.song_id == song_id


def test_updating_rating_does_not_duplicate_notification(app, seed_users_and_song):
    """
    Rating the same song twice (updating the score) should only
    send one notification per rating action.
    """
    with app.app_context():
        sharer_id = seed_users_and_song["sharer"].id
        rater_id = seed_users_and_song["rater"].id
        song_id = seed_users_and_song["song"].id

        rate_song(rater_id, song_id, 3)
        rate_song(rater_id, song_id, 5)

        notifications = get_notifications(sharer_id)
        # Each call to rate_song fires one notification, so 2 total
        assert len(notifications) == 2
