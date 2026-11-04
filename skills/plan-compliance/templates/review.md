{{! 合规审核意见（结论由代码按红线预检和审核项给出）。数据：panel、as_of_text、record_ids、review（review.py 的输出）、articles（审核对照的条文原文）。 }}
【合规审核】{{review.result.plan_id}}（第 {{review.result.round}} 轮）

结论：{{review.result.verdict_name}}

红线预检（actions，规则引擎）：{{#review.result.redline.ok}}通过{{/review.result.redline.ok}}{{^review.result.redline.ok}}不通过{{/review.result.redline.ok}}

{{#review.result.redline.problems}}
- {{.}}
{{/review.result.redline.problems}}

审核项：

{{#review.result.items}}
- {{label}}：{{conclusion_name}}{{#articles_text}}（{{.}}）{{/articles_text}}{{#note}}。{{.}}{{/note}}
{{/review.result.items}}

{{#review.result.summary}}
审核意见：{{.}}

{{/review.result.summary}}
{{#review.result.has_human_items}}
需要确认人把关：

{{#review.result.human_items}}
- {{.}}
{{/review.result.human_items}}

{{/review.result.has_human_items}}
对照条文（原文）：

{{#articles}}
- {{document}} {{article}}：{{#content}}{{.}}{{/content}}{{^content}}（条文索引里没有这一条）{{/content}}
{{/articles}}

依据：记录 {{record_ids.review}}（审核）；下发时引用本记录编号
