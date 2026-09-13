# Daily 编辑与恢复合同 v2

## 编辑顺序

目标是让内容创作者在约两分钟内看懂值得看的变化。通常3–5条、上限7条，不凑数；确实全为重复或证据不足时允许0条，写emptyReason并记录完成，不发空卡片。

1. 完整读取本次 candidates.json 和最近七天 history.json。历史只含成功发送条目；旧版本记录标记 `legacy_artifacts_may_differ_from_sent_message`，有歧义时通过既有 messageId 用章北海 bot 回读，不把后来改写的 decision 当作实际发送正文。
2. 按“产品/对象＋具体变化”归并事件。相同产品不同能力不是同一事件；同一事件的官宣、转发、报道不是多条新闻。`event` 是本次编辑判断，不伪称官方 canonical id。
3. 每条候选标注 priority：`creation` 为图像/视频/音乐/语音/数字人及直接影响创作的成本、版权、平台规则；`workflow` 为相邻工具/Agent/开源/案例；`industry` 为重大行业变化。按本条实际内容判断，关键词只帮助检查，不能决定入选。
4. 标注 importance：`major` 为显著能力/可用范围/成本/权利边界变化；`useful` 为有参考价值的增量；`low` 为泛宣传、转发或信息微弱。先审查 creation，再 workflow、industry；官方大公司或高热度不自动获得优先权。
5. 与历史比较：重复内容引用 historyRef；新进展说明此前已知内容、本次具体 delta。新来源或转发不等于新进展。历史中无相应事件时写首次报道；首次报道指本日报首次报道，不能据此声称今天发布。
6. 在写正文前，复查所有未入选的 creation 候选。重要创作候选不得用“相关性低”“篇幅有限”笼统舍弃。需要基于来源说明证据不足、没有增量、可用性不明确，或说明哪条更重要的创作事件占用了已满的7条名额。可用性不明确不是强制淘汰理由：重要预告仍可入选，但须写预告/未知条件。
7. 每条正文写 change（新变化与条件）、insight（为何值得看）。推断须用“可能/尚待验证”等准确限定；不得凭标题扩写性能、商用授权、免费范围或发布日期。`evidence` 为候选标题/摘要的原文摘录；引用检查只能验证出处，不能自动证明整句推断合理。
8. `items` 按 creation→workflow→industry 排序，同级按重要性和增量排。行业新闻可以占主要篇幅，但前提是当天重要创作候选已充分审查。没有固定栏目配额，不生成万能导语或强行主线。

## decision.json

从 `decision-template.json` 复制 schemaVersion/date/sourceHash/historyHash。取舍记录覆盖全部 key；正文仅放入选事件。标题、来源、链接由脚本取原候选，不手写。

```json
{
  "schemaVersion": 2,
  "date": "YYYY-MM-DD",
  "sourceHash": "复制模板",
  "historyHash": "复制模板",
  "reviews": [{
    "key": "候选key",
    "event": "产品与具体变化",
    "priority": "creation",
    "importance": "major",
    "disposition": "include",
    "reason": "结合候选信息解释为何选择或舍弃"
  }],
  "items": [{
    "key": "候选key",
    "topic": "图像生成",
    "changeType": "new",
    "change": "事实性的新变化，包含已知开放范围或限制。",
    "insight": "这项变化为什么值得内容创作者了解。",
    "evidence": [{"key": "候选key", "quote": "从该候选标题或摘要摘取原文"}]
  }]
}
```

- disposition：include / merge / duplicate / defer / omit。
- merge：写 mergedInto，指向同 event 的 include key；合并证据可引用该事件的其它候选。
- duplicate：写 historyRef，原样复制历史 ref，不得编造。历史记录有歧义先回读。
- update：review 写 historyRef，item 写 `changeType: update` 和 delta（12–240字）。已发送过同链接时只能按有证据的update再次入选。
- creation+major 被 omit/defer：写 reasonCode（insufficient_evidence / not_new / not_available / capacity）与 coverageReview（20–240字）；not_new 必须引用历史，capacity 必须已选7条且 preferredKey 指向更优先的重大创作事件。此门槛要求解释，不能把它变成理由模板照填。
- reason：12–240字；change/insight：各12–140字；证据摘录6–300字，严格匹配同事件候选标题或摘要。全部跳过写emptyReason（20–240字）。
- 合法topic沿用 contracts.md 的词表。
