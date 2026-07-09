"""
tests/test_notifications.py — Mixtape

Tests for notification creation logic.
"""

import pytest
from app import create_app, db
from models import User, Song
from services.notification_service import rate_song, get_notifications


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


def test_rating_friend_song_creates_notification(app):
    """
    Rating a song shared by another user should notify the song sharer.
    """
    with app.app_context():
        sharer = User(username="aaliya", email="aaliya@example.com")
        rater = User(username="kenji", email="kenji@example.com")
        db.session.add_all([sharer, rater])
        db.session.flush()

        song = Song(title="Shared Song", artist="The Sharer", shared_by=sharer.id)
        db.session.add(song)
        db.session.commit()

        rating = rate_song(rater.id, song.id, 5)
        notifications = get_notifications(sharer.id)

        assert rating.score == 5
        assert len(notifications) == 1
        assert notifications[0]["type"] == "song_rated"
        assert "kenji" in notifications[0]["body"]
        assert "Shared Song" in notifications[0]["body"]
        assert "5" in notifications[0]["body"]


def test_rating_own_song_does_not_create_notification(app):
    """
    Rating your own shared song should not notify yourself.
    """
    with app.app_context():
        user = User(username="aaliya", email="aaliya@example.com")
        db.session.add(user)
        db.session.flush()

        song = Song(title="Self Rated Song", artist="The Sharer", shared_by=user.id)
        db.session.add(song)
        db.session.commit()

        rate_song(user.id, song.id, 4)
        notifications = get_notifications(user.id)

        assert notifications == []
