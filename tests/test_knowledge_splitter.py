from mewhelp.knowledge.splitter import split_markdown


def test_headings_supply_question_and_parent_category():
    chunks = split_markdown("# 售后手册\n## 退货\n### 运费说明\n满 99 元包邮。")
    assert len(chunks) == 1
    assert chunks[0].questions == "运费说明"
    assert chunks[0].category == "售后手册 / 退货"
    assert chunks[0].section_path == "售后手册 / 退货 / 运费说明"


def test_long_prose_splits_on_sentence_boundaries_with_overlap():
    text = "# 规则\n## 处理\n" + "第一句话说明流程。第二句话说明材料。第三句话说明时限。第四句话说明结果。"
    chunks = split_markdown(text, max_chars=34, overlap_chars=12)
    assert len(chunks) >= 2
    assert all(c.answer.endswith("。") for c in chunks)
    assert chunks[0].answer.split("。")[-2] in chunks[1].answer
    assert all(c.answer for c in chunks)


def test_large_table_repeats_header_and_keeps_whole_rows():
    text = "# 商品\n## 尺码\n| 型号 | 尺寸 |\n| --- | --- |\n| A | 小号 |\n| B | 中号 |\n| C | 大号 |"
    chunks = split_markdown(text, max_chars=45, overlap_chars=0)
    assert len(chunks) >= 2
    assert all("| 型号 | 尺寸 |" in c.answer for c in chunks)
    assert all("| --- | --- |" in c.answer for c in chunks)
    assert sum("| A | 小号 |" in c.answer for c in chunks) == 1


def test_neighbor_pointers_follow_source_order_across_sections():
    chunks = split_markdown("# A\n## B\n第一句。第二句。第三句。\n## C\n另一句。", max_chars=12, overlap_chars=0)
    b = [c for c in chunks if c.questions == "B"]
    c = [c for c in chunks if c.questions == "C"]
    assert b[-1].next_key == c[0].key
    assert c[0].prev_key == b[-1].key


def test_overlong_sentence_is_kept_whole_and_flagged():
    chunks = split_markdown("# 政策\n## 条款\n" + "甲" * 50 + "。", max_chars=20)
    assert chunks[0].answer == "甲" * 50 + "。"
    assert chunks[0].oversize


def test_explicit_key_clause_marker_is_metadata_only():
    chunks = split_markdown("# 政策\n## 退款\n<!-- key-clause -->\n退款申请须在七天内提交。")
    assert chunks[0].is_key_clause
    assert "key-clause" not in chunks[0].answer


def test_pipe_line_outside_table_does_not_hang():
    chunks = split_markdown("# 说明\n| 这不是表格 |")
    assert chunks[0].answer == "| 这不是表格 |"


def test_skipped_heading_levels_keep_ancestors():
    chunks = split_markdown("# 政策\n### 运费\n满 99 元包邮。")
    assert chunks[0].section_path == "政策 / 运费"


def test_repeated_section_title_has_distinct_stable_keys():
    chunks = split_markdown("# 政策\n## 运费\n第一版。\n## 运费\n第二版。")
    assert len({chunk.key for chunk in chunks}) == 2
    assert chunks[0].next_key == chunks[1].key


def test_overlap_is_dropped_when_next_sentence_would_exceed_limit():
    chunks = split_markdown("# A\n## B\n第一句很短。第二句话也很短。第三句话也很短。", max_chars=18, overlap_chars=15)
    assert all(len(c.answer) <= 18 for c in chunks)
