# Parameter Dependencies

Canonical cross-parameter rules reference for both the CloudFormation and Terraform deployments of the Amazon Connect Operational Review stack.

**Audience:** For deployers (customers/TAMs) using either the CloudFormation or Terraform deployment. Referenced from `deploy/cloudformation/README.md` and `deploy/terraform/README.md`.

## How to read this file

Each rule below is stated in an **IF / THEN / BECAUSE** form: IF a given parameter (or variable) is set to a particular value, THEN some other parameter/variable must (or should) also be set to a particular value, BECAUSE of a specific, code-grounded reason. Every rule also names the observable **failure mode** a deployer will see if the rule is violated.

The **Applies to** column indicates which deployment method(s) the rule governs:

- `Both` — the rule applies to both the CloudFormation template and the Terraform module. Parameter names are given in both spellings (e.g. `EnableGlueCatalog` / `enable_glue_catalog`).
- `CloudFormation only` — the rule is specific to the CloudFormation deployment (no equivalent surface exists in the Terraform module).
- `Terraform only` — the rule is specific to the Terraform deployment (no equivalent surface exists in the CloudFormation template).

Each rule has a stable anchor (`#d1` through `#d5`) so deployment READMEs and other docs can deep-link to individual rules.

## Parameter Dependencies

| ID | Applies to | If … | Then … | Reason | Failure mode if violated |
|----|------------|------|--------|--------|--------------------------|
| <a id="d1"></a>D1 | Both | `EnableGlueCatalog=Yes` / `enable_glue_catalog=true` | `RetainJsonData=Yes` / `retain_json_data=true` | The state machine's `DeleteJsonData` state deletes the S3 prefixes under `s3://<bucket>/data/…` that back the Glue tables when `retainJsonData=false`. With Glue enabled but JSON not retained, the tables point at empty prefixes after every successful run. | Athena queries against the Glue tables return zero rows after the first successful run. |
| <a id="d2"></a>D2 | Both | `EnableHtmlReport=No` / `enable_html_report=false` | Raw JSON in `s3://<bucket>/data/…` is the only output; `RetainJsonData` / `retain_json_data` becomes a no-op in this configuration. | The state machine's `CheckGenerateReport` Choice takes the `SkipReport` branch and terminates the execution before `DeleteJsonData` is ever invoked, so raw JSON is retained regardless of the `RetainJsonData` / `retain_json_data` setting. | A reader who disabled the HTML report assumes the stack produces no output and misses the raw JSON artifacts under `s3://<bucket>/data/…`. |
| <a id="d3"></a>D3 | Both | `EnableReviewSchedule=No` / `enable_review_schedule=false` | `ReviewScheduleExpression`, `ReviewScheduleTimezone` (both), and `review_flexible_window_minutes` (Terraform only) are no-ops. | The CloudFormation `ScheduleEnabled` Condition and the Terraform `count` / `for_each` guards skip creation of the EventBridge scheduler and its IAM role entirely when the schedule is disabled, so none of the schedule configuration parameters are read. | Schedule-related values are silently ignored; the deployer sets a cron expression / timezone / window and no schedule is created. |
| <a id="d4"></a>D4 | Both | `EnableHtmlReport=No` / `enable_html_report=false` | `DevOpsAgentAgentSpaceId` / `devops_agent_agent_space_id` has no effect. | The AgentSpace ID is consumed only by the `ReportGeneratorFunction`, which is the Lambda that renders the HTML report and injects the DevOps Agent buttons. When HTML generation is disabled, that Lambda's output is never produced. | The deployer configures a DevOps Agent that never receives an HTML report to inject buttons into. |
| <a id="d5"></a>D5 | Terraform only | `review_flexible_window_minutes > 0` | The schedule fires at a random offset within the window instead of at a fixed time; this is useful for cron expressions fanning out across many stacks but undesirable for one-off tests. | `parallel.tf` sets `mode = var.review_flexible_window_minutes > 0 ? "FLEXIBLE" : "OFF"` on the `aws_scheduler_schedule` resource. Any positive value activates the flexible-window mode. | A test stack's schedule fires at an unexpected time within the configured window, making it hard to correlate with a specific wall-clock time. |

## Configuration precedence

For the two runtime-configurable flags — `retainJsonData` and `generateHtmlReport` — the `prepare_context` Lambda resolves each value using this precedence chain:

```
execution input  >  SSM parameter (/connect-ops-review/config)  >  default in code
```

- **Execution input** wins when a key is present in the Step Functions execution input (for example, a manual `StartExecution` call that passes `{"retainJsonData": false}`).
- **SSM parameter** — the JSON document at `/connect-ops-review/config` — is the post-deploy control surface for `retainJsonData` and `generateHtmlReport`. Editing SSM changes behavior on the next run without a redeploy.
- **Default in code** applies only when neither of the above supplies a value.

As of v2.0.1-rc.7, scheduled runs no longer carry `retainJsonData` or `generateHtmlReport` in their EventBridge Scheduler input, so post-deploy SSM edits take effect on the next scheduled run. (Previously the scheduler input pinned `retainJsonData`, causing SSM edits to be silently overridden on scheduled executions — see [QB-72](../QUALITY_BACKLOG.md).)

**Runtime enforcement.** After precedence resolution, `src/lambda/prepare_context.py` applies the D1 / D2 invariants as a final coercion step: if `enableGlueCatalog=true` or `generateHtmlReport=false`, `retainJsonData` is forced to `true` regardless of what execution input or SSM asked for. This guarantees D1 / D2 hold on every run, even if a caller supplies an inconsistent combination.

## Per-rule details

### <a id="d1-details"></a>D1 — Glue ↔ JSON retention

When Glue Data Catalog integration is enabled (`EnableGlueCatalog=Yes` on CloudFormation, `enable_glue_catalog=true` on Terraform), the stack provisions a Glue database and a set of Glue tables partitioned over `s3://<bucket>/data/<componentType>/year=…/month=…/day=…/`. Those tables are only useful as long as the underlying S3 prefixes contain data.

At the end of a successful review run, the state machine evaluates whether the raw JSON should be preserved. When `retainJsonData=false` and the HTML report ran successfully, the machine enters the `DeleteJsonData` state, which deletes exactly the S3 prefixes that back the Glue tables. With Glue enabled and JSON not retained, this happens after every run, leaving Glue tables that point at empty prefixes.

To keep the Glue tables queryable across runs, set `RetainJsonData=Yes` / `retain_json_data=true` whenever Glue is enabled.

### <a id="d2-details"></a>D2 — HTML disabled ⇒ JSON is the only output

When the HTML report is disabled (`EnableHtmlReport=No` / `enable_html_report=false`), the state machine reaches its `CheckGenerateReport` Choice state and takes the `SkipReport` branch. That branch terminates the execution before `DeleteJsonData` is ever invoked.

As a consequence, the raw JSON artifacts under `s3://<bucket>/data/…` are the only output the stack produces in this configuration, regardless of what `RetainJsonData` / `retain_json_data` is set to. The retention parameter effectively becomes a no-op: JSON is always retained when HTML generation is off, because the code path that deletes it never runs.

Deployers who disable the HTML report should point their downstream tooling (Athena via Glue, or direct S3 consumers) at the JSON prefixes.

### <a id="d3-details"></a>D3 — Schedule-enable gate

The schedule enablement flag gates the creation of all scheduler-related infrastructure:

- On CloudFormation, the `ScheduleEnabled` Condition guards the `AWS::Scheduler::Schedule` resource and its IAM role.
- On Terraform, `count` (and `for_each` where applicable) guards the `aws_scheduler_schedule.review_schedule` resource and its supporting IAM role in `parallel.tf`.

When the schedule is disabled, none of the schedule configuration parameters — `ReviewScheduleExpression`, `ReviewScheduleTimezone`, and (on Terraform only) `review_flexible_window_minutes` — are consumed by any resource. Setting them has no effect. This is the intended behavior, but it can surprise a deployer who sets a cron expression and then wonders why no schedule appears in the EventBridge console.

To trigger the state machine on a schedule, both enable the schedule (`EnableReviewSchedule=Yes` / `enable_review_schedule=true`) AND set the desired expression / timezone / window.

### <a id="d4-details"></a>D4 — DevOps Agent ↔ HTML report

The DevOps Agent integration is delivered by the `ReportGeneratorFunction` Lambda: when the Lambda renders the HTML report, it injects deep-links / buttons that hand the current findings off to a configured Amazon Q Developer AgentSpace. The AgentSpace ID (`DevOpsAgentAgentSpaceId` / `devops_agent_agent_space_id`) is read only by this Lambda.

When HTML generation is disabled (see D2 and D4's `If` condition above), the `ReportGeneratorFunction` never produces its output, and the AgentSpace ID is never consumed. Configuring a DevOps Agent while leaving the HTML report disabled produces no observable effect — the agent is wired up correctly but is never invoked from a report, because no report is generated.

To use the DevOps Agent integration, both keep the HTML report enabled (`EnableHtmlReport=Yes` / `enable_html_report=true`) AND set the AgentSpace ID.

### <a id="d5-details"></a>D5 — Terraform flexible window ↔ schedule

On Terraform, `review_flexible_window_minutes` controls the EventBridge Scheduler flexible-time-window mode. `parallel.tf` sets the mode conditionally:

```
mode = var.review_flexible_window_minutes > 0 ? "FLEXIBLE" : "OFF"
```

With the default value of `0`, the mode is `OFF` and the schedule fires at the exact time specified by `review_schedule_expression`. With any positive value, the mode switches to `FLEXIBLE` and the schedule fires at a random moment within the specified window (in minutes) after the scheduled time.

Flexible windows are useful when many independently-deployed stacks share the same cron expression and would otherwise all fire simultaneously; the random offset spreads their invocations out. Conversely, in a single test stack the flexible window makes it harder to correlate an execution with a specific wall-clock time, so leaving `review_flexible_window_minutes=0` is preferable for testing.

This rule applies only to the Terraform deployment. The CloudFormation template does not expose an equivalent parameter today.
