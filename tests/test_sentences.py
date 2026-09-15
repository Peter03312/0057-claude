from app.sentences import split_sentences, tail_sentences


def test_basic_split():
    assert split_sentences("你好。世界！") == ["你好。", "世界！"]


def test_consecutive_terminators_belong_to_same_sentence():
    assert split_sentences("太好了！！！真的吗？！") == ["太好了！！！", "真的吗？！"]


def test_unclosed_fragment_not_counted():
    assert split_sentences("他走了。然后") == ["他走了。"]
    assert split_sentences("没有句末符号") == []


def test_mixed_terminators_and_trailing_fragment():
    assert split_sentences("第一。第二！？第三") == ["第一。", "第二！？"]


def test_leading_terminators_form_one_sentence():
    assert split_sentences("。！？然后没了") == ["。！？"]


def test_latin_period_does_not_split():
    assert split_sentences("Mr. Li来了。版本1.5很好！") == ["Mr. Li来了。", "版本1.5很好！"]


def test_tail_sentences():
    text = "一。二！三？四"
    assert tail_sentences(text, 2) == ["二！", "三？"]
    assert tail_sentences(text, 99) == ["一。", "二！", "三？"]
    assert tail_sentences(text, 1) == ["三？"]
    assert tail_sentences(text, 0) == []
