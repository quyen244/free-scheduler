from profiles import HunyuanMTProfile, LMT60Profile, LMT60SubtitleProfile, resolve_profile


def test_lmt_profile_uses_explicit_source_and_target_labels():
    prompt = LMT60Profile().prompt("Hello.", "en", "vi", None)

    assert "from English into Vietnamese" in prompt
    assert prompt.endswith("Vietnamese:")


def test_lmt_profile_keeps_the_budget_in_the_prompt():
    prompt = LMT60Profile().prompt("你好", "zh", "vi", 99)

    assert "from Chinese into Vietnamese" in prompt
    assert "99 characters" in prompt


def test_subtitle_profile_preserves_meaning_and_speech_constraints():
    prompt = LMT60SubtitleProfile().prompt("Hello.", "en", "vi", 77)

    assert "Preserve facts, names, numbers, uncertainty and tone" in prompt
    assert "concise, natural spoken Vietnamese" in prompt
    assert "77 Vietnamese characters" in prompt
    assert LMT60SubtitleProfile().budget_first_pass is True


def test_lmt_profile_is_greedy_and_reproducible():
    assert LMT60Profile().sampling(123) == {"temperature": 0.0, "seed": 123}


def test_existing_hunyuan_profile_still_uses_chinese_prompt():
    assert "越南语" in HunyuanMTProfile().prompt("你好", "zh", "vi", None)


def test_unknown_profile_is_rejected_before_a_job_starts():
    try:
        resolve_profile("not-a-model")
    except ValueError as exc:
        assert "TRANSLATE_PROFILE" in str(exc)
    else:
        raise AssertionError("unknown profile was accepted")
