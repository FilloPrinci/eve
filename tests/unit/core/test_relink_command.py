import pytest

from easy_video_editor.core.commands import RelinkClip
from easy_video_editor.core.errors import CommandError
from easy_video_editor.core.session import ProjectSession

from .conftest import AI, MEDIA, USER


def test_relink_updates_path_and_fingerprint(session: ProjectSession) -> None:
    new_path = str(MEDIA / "moved" / "clip.mp4")
    session.execute(RelinkClip(clip_id="clip", path=new_path, fingerprint="v1:1:2:abc"), USER)
    clip = session.project.clip("clip")
    assert (clip.path, clip.fingerprint) == (new_path, "v1:1:2:abc")


def test_relink_is_user_only_and_needs_absolute_path(session: ProjectSession) -> None:
    with pytest.raises(CommandError, match="Only the user"):
        session.execute(RelinkClip(clip_id="clip", path=str(MEDIA / "x.mp4")), AI)
    with pytest.raises(CommandError, match="absolute"):
        session.execute(RelinkClip(clip_id="clip", path="x.mp4"), USER)
