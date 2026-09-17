# AI Analyzer Lambda Function
#
# Standalone Lambda that performs AI/Q Connect analysis for Amazon Connect
# instances, checking agent inventory, prompt configuration, guardrails,
# domain encryption, and agent logging.
# Extracted from the monolithic lambda_function.py as part of the
# parallel orchestration architecture.
#
# Uses shared utilities from analyzer_common and graceful_timeout modules
# for standardized input validation, result formatting, S3 persistence,
# and time budget management.

import boto3
import logging
import time
from botocore.exceptions import ClientError

from analyzer_common import (
    validate_input,
    success_result,
    error_result,
    persist_to_s3,
    discover_assistant_id,
    paginate_api_call,
)
from graceful_timeout import compute_time_budget, check_time_budget, TimeBudgetExceeded

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Default time budget if maxSeconds not provided in event payload
DEFAULT_MAX_SECONDS = 540

# Total number of AI checks (excluding assistant discovery)
TOTAL_CHECKS = 5


# ---------------------------------------------------------------------------
# Check function stubs (full implementations in tasks 2.1–2.6)
# ---------------------------------------------------------------------------


def check_ai_agent_inventory(
    qconnect_client, connect_client, assistant_id, instance_id, start_time, time_budget
):
    """Check AI agent inventory and configuration.

    Args:
        qconnect_client: boto3 QConnect client.
        connect_client: boto3 Connect client.
        assistant_id: The Q Connect assistant ID.
        instance_id: The Connect instance ID.
        start_time: Epoch timestamp when execution began.
        time_budget: Allowed time budget in seconds.

    Returns:
        dict: Structured finding with check_name, status, detail, recommendation, data.
              data['_raw_agents'] contains the raw API agent list for reuse by other checks.
    """
    logger.info(
        f"[O1] Starting AI Agent Inventory check for assistant_id={assistant_id}"
    )

    # List all AI agents
    check_time_budget(start_time, time_budget)
    agents = paginate_api_call(
        qconnect_client.list_ai_agents,
        "aiAgentSummaries",
        start_time,
        time_budget,
        assistantId=assistant_id,
        maxResults=100,
    )
    logger.info(f"[O1] list_ai_agents returned {len(agents)} agent(s)")

    if not agents:
        logger.warning(f"[O1] No AI agents found for assistant {assistant_id}")
        return {
            "check_name": "agent_inventory",
            "status": "info",
            "detail": "No AI agents configured for this assistant.",
            "recommendation": "Consider configuring AI agents to leverage Amazon Q in Connect capabilities.",
            "data": {
                "agent_count": 0,
                "agents": [],
                "mcp_heavy_agents": [],
                "security_profiles_with_apps": 0,
            },
        }

    # Build security profile map with their MCP applications
    sp_apps_map = {}
    try:
        check_time_budget(start_time, time_budget)
        sp_resp = connect_client.list_security_profiles(
            InstanceId=instance_id, MaxResults=100
        )
        for sp in sp_resp.get("SecurityProfileSummaryList", []):
            check_time_budget(start_time, time_budget)
            sp_id = sp["Id"]
            sp_name = sp.get("Name", sp_id)
            apps = []
            try:
                app_resp = connect_client.list_security_profile_applications(
                    InstanceId=instance_id, SecurityProfileId=sp_id, MaxResults=100
                )
                apps = app_resp.get("Applications", [])
            except Exception:
                pass
            if apps:
                sp_apps_map[sp_id] = {"name": sp_name, "apps": apps}
                logger.info(
                    f"[O1] Security profile '{sp_name}' has {len(apps)} MCP app block(s)"
                )
    except TimeBudgetExceeded:
        raise
    except Exception as sp_err:
        logger.warning(f"[O1] Could not list security profiles: {sp_err}")

    # Iterate over each agent to get details
    agents_data = []
    mcp_heavy_agents = []
    for idx, agent in enumerate(agents):
        check_time_budget(start_time, time_budget)
        a_id = agent.get("aiAgentId", "")

        # Rate limiting: pause between consecutive get_ai_agent calls
        if idx > 0:
            time.sleep(0.3)

        try:
            check_time_budget(start_time, time_budget)
            detail = qconnect_client.get_ai_agent(
                assistantId=assistant_id, aiAgentId=a_id
            )
            ai_agent = detail.get("aiAgent", {})
            name = ai_agent.get("name", "N/A")
            a_type = ai_agent.get("type", "N/A")
            status = agent.get("status", "N/A")

            # Extract tools from configuration
            config = ai_agent.get("configuration", {})
            logger.info(f"[O1] Agent '{name}' config keys: {list(config.keys())}")
            orch = config.get("orchestrationAIAgentConfiguration", {})
            logger.info(f"[O1] Agent '{name}' orch keys: {list(orch.keys())}")
            tools = orch.get("toolConfigurations", [])
            logger.info(
                f"[O1] Agent '{name}' has {len(tools)} tool(s) in toolConfigurations"
            )

            # Categorize tools
            mcp_tools = []
            builtin_tools = []
            for t in tools:
                t_type = t.get("toolType", "UNKNOWN")
                t_name = t.get("toolName", t.get("title", "unknown"))
                logger.debug(f"[O1]   Tool: type={t_type}, name={t_name}")
                if t_type == "MODEL_CONTEXT_PROTOCOL":
                    mcp_tools.append(t_name)
                else:
                    builtin_tools.append(t_name)

            # Track MCP-heavy agents for sprawl insight
            if len(mcp_tools) > 15:
                mcp_heavy_agents.append({"name": name, "mcp_count": len(mcp_tools)})

            # Find security profile - match by checking which profile has MCP apps
            # whose namespace matches tools in this agent
            matched_sp = None
            agent_tool_ids = set()
            for t in tools:
                tid = t.get("toolId", "")
                if tid:
                    agent_tool_ids.add(tid)
                    # Also extract namespace from toolId like "gateway_NAMESPACE__TARGET___op"
                    if "gateway_" in tid:
                        ns = tid.split("__")[0].replace("gateway_", "")
                        agent_tool_ids.add(ns)

            if agent_tool_ids:
                for sp_id, sp_info in sp_apps_map.items():
                    matched = False
                    for app in sp_info["apps"]:
                        ns = app.get("Namespace", "")
                        if ns and any(ns in tid for tid in agent_tool_ids):
                            matched = True
                            break
                    if matched:
                        matched_sp = sp_info["name"]
                        logger.info(
                            f"[O1] Agent '{name}' matched security profile '{sp_info['name']}'"
                        )
                        break

            logger.info(
                f"[O1] Agent: {name}, mcp={len(mcp_tools)}, builtin={len(builtin_tools)}, sp={matched_sp}"
            )

            agents_data.append(
                {
                    "name": name,
                    "type": a_type,
                    "status": status,
                    "mcp_tool_count": len(mcp_tools),
                    "builtin_tool_count": len(builtin_tools),
                    "tool_names": (mcp_tools + builtin_tools)[
                        :20
                    ],  # Cap to avoid oversized payloads
                    "security_profile": matched_sp,
                }
            )

        except TimeBudgetExceeded:
            raise
        except Exception as detail_err:
            logger.warning(f"[O1] Failed to get detail for agent {a_id}: {detail_err}")
            agents_data.append(
                {
                    "name": agent.get("name", "N/A"),
                    "type": "N/A",
                    "status": agent.get("status", "N/A"),
                    "mcp_tool_count": 0,
                    "builtin_tool_count": 0,
                    "tool_names": [],
                    "security_profile": None,
                }
            )

    # Determine status and build detail string
    if mcp_heavy_agents:
        status = "warn"
        sprawl_parts = []
        for a in mcp_heavy_agents:
            severity = "CRITICAL" if a["mcp_count"] > 30 else "HIGH"
            sprawl_parts.append(f"{a['name']}: {a['mcp_count']} MCP tools ({severity})")
        detail = (
            f"Found {len(agents)} AI agent(s). "
            f"MCP Tool Sprawl detected - {len(mcp_heavy_agents)} agent(s) exceed 15 MCP tools: "
            + "; ".join(sprawl_parts)
        )
        recommendation = (
            "Consider creating role-specific security profiles per AI agent instead of one with all tools. "
            "Reducing unused MCP tool permissions can improve response latency. "
            "A larger tool space directly increases the tool-selection phase during each inference cycle."
        )
    else:
        status = "pass"
        detail = f"Found {len(agents)} AI agent(s). No MCP tool sprawl issues detected."
        recommendation = None

    # Truncate detail to 1024 chars
    if len(detail) > 1024:
        detail = detail[:1021] + "..."

    return {
        "check_name": "agent_inventory",
        "status": status,
        "detail": detail,
        "recommendation": recommendation,
        "data": {
            "agent_count": len(agents),
            "agents": agents_data,
            "mcp_heavy_agents": mcp_heavy_agents,
            "security_profiles_with_apps": len(sp_apps_map),
            "_raw_agents": agents,  # Pass-through for reuse by guardrails check
        },
    }


def check_ai_prompt_configuration(
    qconnect_client, assistant_id, start_time, time_budget
):
    """Check AI prompt configuration and customization.

    Args:
        qconnect_client: boto3 QConnect client.
        assistant_id: The Q Connect assistant ID.
        start_time: Epoch timestamp when execution began.
        time_budget: Allowed time budget in seconds.

    Returns:
        dict: Structured finding with check_name, status, detail, recommendation, data.
    """
    logger.info(
        f"[O2] Starting AI Prompt Configuration check for assistant_id={assistant_id}"
    )
    try:
        check_time_budget(start_time, time_budget)
        prompts = paginate_api_call(
            qconnect_client.list_ai_prompts,
            "aiPromptSummaries",
            start_time,
            time_budget,
            assistantId=assistant_id,
            maxResults=100,
        )
        logger.info(f"[O2] list_ai_prompts returned {len(prompts)} prompt(s)")

        if not prompts:
            return {
                "check_name": "prompt_configuration",
                "status": "info",
                "detail": "No AI prompts configured.",
                "recommendation": "Consider configuring AI prompts to customize Amazon Q in Connect behavior.",
                "data": {"prompt_count": 0, "prompts": [], "model_distribution": {}},
            }

        # Iterate over each prompt to get details
        prompts_data = []
        model_summary = {}
        for idx, p in enumerate(prompts):
            check_time_budget(start_time, time_budget)
            p_id = p.get("aiPromptId", "")

            # Rate limiting: pause between consecutive get_ai_prompt calls
            if idx > 0:
                time.sleep(0.3)

            try:
                check_time_budget(start_time, time_budget)
                detail_resp = qconnect_client.get_ai_prompt(
                    assistantId=assistant_id, aiPromptId=p_id
                )
                prompt_detail = detail_resp.get("aiPrompt", {})
                name = prompt_detail.get("name", "N/A")
                p_type = prompt_detail.get("type", "N/A")
                model_id = prompt_detail.get("modelId", "N/A")
                template_config = prompt_detail.get("templateConfiguration", {})
            except TimeBudgetExceeded:
                raise
            except Exception:
                name = p.get("name", "N/A")
                p_type = "N/A"
                model_id = "N/A"
                template_config = {}

            # Extract template text for completeness validation
            template_text = ""
            if template_config:
                # TEXT type: templateConfiguration.textFullAIPromptEditTemplateConfiguration.text
                text_config = template_config.get(
                    "textFullAIPromptEditTemplateConfiguration", {}
                )
                template_text = text_config.get("text", "")

            # Track model distribution
            short_model = model_id.split(":")[0] if ":" in model_id else model_id
            model_summary[short_model] = model_summary.get(short_model, 0) + 1

            prompts_data.append(
                {
                    "name": name,
                    "type": p_type,
                    "model_id": model_id,
                    "has_template_text": bool(template_text and template_text.strip()),
                }
            )

        # Check prompt completeness - identify prompts with missing/empty template text
        incomplete_prompts = [
            p for p in prompts_data if not p.get("has_template_text", False)
        ]

        # Build model distribution summary string for detail
        model_parts = []
        for model, count in sorted(
            model_summary.items(), key=lambda x: x[1], reverse=True
        ):
            model_parts.append(f"{model}: {count}")
        model_dist_str = ", ".join(model_parts)

        if incomplete_prompts:
            # Some prompts are missing system prompt / have empty instruction
            incomplete_names = [p["name"] for p in incomplete_prompts]
            detail = (
                f"Found {len(prompts)} AI prompt(s). "
                f"{len(incomplete_prompts)} prompt(s) missing system prompt or have empty instruction: "
                + ", ".join(incomplete_names)
                + f". Model distribution: {model_dist_str}"
            )
            recommendation = (
                "Review and configure system prompts for all AI prompts. "
                "Each prompt should have substantive instruction text to guide AI behavior. "
                "Empty or missing prompts may result in generic or unpredictable responses."
            )
            status = "warn"
        else:
            # All prompts are complete
            detail = f"Found {len(prompts)} AI prompt(s), all with valid configurations. Model distribution: {model_dist_str}"
            recommendation = None
            status = "pass"

        # Truncate detail to 1024 chars
        if len(detail) > 1024:
            detail = detail[:1021] + "..."

        return {
            "check_name": "prompt_configuration",
            "status": status,
            "detail": detail,
            "recommendation": recommendation,
            "data": {
                "prompt_count": len(prompts),
                "prompts": prompts_data,
                "model_distribution": model_summary,
                "incomplete_prompts": [p["name"] for p in incomplete_prompts],
            },
        }
    except TimeBudgetExceeded:
        raise
    except Exception as e:
        logger.error(f"[O2] Error: {e}")
        error_detail = f"Error retrieving AI prompt configuration: {str(e)}"
        if len(error_detail) > 1024:
            error_detail = error_detail[:1021] + "..."
        return {
            "check_name": "prompt_configuration",
            "status": "error",
            "detail": error_detail,
            "recommendation": None,
            "data": None,
        }


def check_q_guardrails(
    qconnect_client, assistant_id, start_time, time_budget, cached_agents=None
):
    """Check Q Connect guardrail configuration and coverage.

    Args:
        qconnect_client: boto3 QConnect client.
        assistant_id: The Q Connect assistant ID.
        start_time: Epoch timestamp when execution began.
        time_budget: Allowed time budget in seconds.
        cached_agents: Optional pre-fetched agents list to avoid redundant API call.

    Returns:
        dict: Structured finding with check_name, status, detail, recommendation, data.
    """
    logger.info(f"[S1] Starting Q Guardrails check for assistant_id={assistant_id}")
    try:
        # --- Part 1: List and analyze guardrail configurations ---
        check_time_budget(start_time, time_budget)
        guardrails = paginate_api_call(
            qconnect_client.list_ai_guardrails,
            "aiGuardrailSummaries",
            start_time,
            time_budget,
            assistantId=assistant_id,
            maxResults=100,
        )
        logger.info(f"[S1] list_ai_guardrails returned {len(guardrails)} guardrail(s)")

        if not guardrails:
            return {
                "check_name": "guardrails",
                "status": "info",
                "detail": "No guardrails configured for this assistant.",
                "recommendation": "Consider configuring AI guardrails if using Amazon Q in Connect agents.",
                "data": {
                    "guardrail_count": 0,
                    "published_count": 0,
                    "guardrail_details": [],
                    "missing_filters": [],
                    "total_agents": 0,
                    "agents_with_guardrail_published": 0,
                    "agents_with_guardrail_draft_only": 0,
                    "agents_without_guardrail": 0,
                    "unprotected_agents": [],
                },
            }

        published = [g for g in guardrails if g.get("visibilityStatus") == "PUBLISHED"]

        # Get detailed config for each guardrail to check filter coverage
        guardrail_details = []
        for idx, g in enumerate(guardrails):
            check_time_budget(start_time, time_budget)
            g_id = g.get("aiGuardrailId", "")

            # Rate limiting between consecutive get_ai_guardrail calls
            if idx > 0:
                time.sleep(0.3)

            detail = {}
            try:
                check_time_budget(start_time, time_budget)
                resp = qconnect_client.get_ai_guardrail(
                    assistantId=assistant_id, aiGuardrailId=g_id
                )
                detail = resp.get("aiGuardrail", {})
                logger.info(
                    f"[S1] Guardrail '{g.get('name')}' detail keys: {list(detail.keys())}"
                )
            except TimeBudgetExceeded:
                raise
            except Exception as gd_err:
                logger.warning(
                    f"[S1] Could not get detail for guardrail {g_id}: {gd_err}"
                )

            # Check which filter types are configured
            has_content_filter = bool(
                detail.get("contentPolicyConfig", {}).get("filtersConfig", [])
            )
            has_topic_policy = bool(
                detail.get("topicPolicyConfig", {}).get("topicsConfig", [])
            )
            has_pii_filter = bool(
                detail.get("sensitiveInformationPolicyConfig", {}).get(
                    "piiEntitiesConfig", []
                )
                or detail.get("sensitiveInformationPolicyConfig", {}).get(
                    "regexesConfig", []
                )
            )
            has_word_filter = bool(
                detail.get("wordPolicyConfig", {}).get("wordsConfig", [])
                or detail.get("wordPolicyConfig", {}).get("managedWordListsConfig", [])
            )
            has_grounding = bool(
                detail.get("contextualGroundingPolicyConfig", {}).get(
                    "filtersConfig", []
                )
            )

            guardrail_details.append(
                {
                    "name": g.get("name", "N/A"),
                    "id": g_id,
                    "status": g.get("status", "N/A"),
                    "visibility": g.get("visibilityStatus", "N/A"),
                    "content_filter": has_content_filter,
                    "topic_policy": has_topic_policy,
                    "pii_filter": has_pii_filter,
                    "word_filter": has_word_filter,
                    "grounding": has_grounding,
                }
            )

        # Check for missing critical filters on PUBLISHED guardrails
        missing_filters = []
        for gd in guardrail_details:
            if gd["visibility"] == "PUBLISHED":
                if not gd["content_filter"]:
                    missing_filters.append(f"'{gd['name']}' missing Content Filter")
                if not gd["pii_filter"]:
                    missing_filters.append(f"'{gd['name']}' missing PII Filter")
                if not gd["topic_policy"]:
                    missing_filters.append(f"'{gd['name']}' missing Denied Topics")
                if not gd["grounding"]:
                    missing_filters.append(
                        f"'{gd['name']}' missing Contextual Grounding"
                    )

        # --- Part 2: Check which AI agents have guardrails associated ---
        check_time_budget(start_time, time_budget)
        if cached_agents is not None:
            agents = cached_agents
            logger.info(
                f"[S1] Using cached agents list ({len(agents)} agent(s)) for guardrail cross-reference"
            )
        else:
            agents = paginate_api_call(
                qconnect_client.list_ai_agents,
                "aiAgentSummaries",
                start_time,
                time_budget,
                assistantId=assistant_id,
                maxResults=100,
            )
            logger.info(
                f"[S1] list_ai_agents returned {len(agents)} agent(s) for guardrail cross-reference"
            )

        agents_with_guardrail_published = []
        agents_with_guardrail_draft_only = []
        agents_without_guardrail = []

        for idx, agent in enumerate(agents):
            check_time_budget(start_time, time_budget)
            a_id = agent.get("aiAgentId", "")
            a_name = agent.get("name", "N/A")
            a_visibility = agent.get("visibilityStatus", "")

            # Rate limiting between consecutive get_ai_agent calls
            if idx > 0:
                time.sleep(0.3)

            try:
                check_time_budget(start_time, time_budget)
                latest_resp = qconnect_client.get_ai_agent(
                    assistantId=assistant_id, aiAgentId=a_id
                )
                latest_agent = latest_resp.get("aiAgent", {})

                # Extract guardrail ID from agent configuration
                config = latest_agent.get("configuration", {})
                orch = config.get("orchestrationAIAgentConfiguration", {})
                guardrail_id = orch.get("orchestrationAIGuardrailId", "")
                if not guardrail_id:
                    guardrail_id = orch.get("guardrailId", "")
                if not guardrail_id:
                    ss = config.get("selfServiceAIAgentConfiguration", {})
                    guardrail_id = ss.get("orchestrationAIGuardrailId", "") or ss.get(
                        "guardrailId", ""
                    )

                logger.info(
                    f"[S1] Agent '{a_name}': guardrail='{guardrail_id}', "
                    f"agent_visibility={a_visibility}"
                )

                # Categorize based on guardrail presence and agent visibility
                if guardrail_id and a_visibility == "PUBLISHED":
                    agents_with_guardrail_published.append(
                        {
                            "name": a_name,
                            "guardrail_id": guardrail_id,
                            "status": "PUBLISHED",
                        }
                    )
                elif guardrail_id:
                    agents_with_guardrail_draft_only.append(
                        {
                            "name": a_name,
                            "guardrail_id": guardrail_id,
                            "status": "DRAFT ONLY",
                        }
                    )
                else:
                    agents_without_guardrail.append(a_name)
            except TimeBudgetExceeded:
                raise
            except Exception as agent_err:
                logger.warning(f"[S1] Error checking agent {a_name}: {agent_err}")
                agents_without_guardrail.append(a_name)

        total_agents = len(agents)
        pub_protected = len(agents_with_guardrail_published)
        draft_only = len(agents_with_guardrail_draft_only)
        unprotected = len(agents_without_guardrail)

        # --- Determine status ---
        if not published:
            status = "fail"
            detail = (
                f"No published AI guardrails found (total: {len(guardrails)}, published: 0). "
                f"PII protection and content filtering are not active."
            )
            recommendation = (
                "Deploy at least one guardrail with all five filter types enabled "
                "(Content Filter, Denied Topics, PII Filter, Word Filter, Contextual Grounding)."
            )
        elif missing_filters or draft_only > 0 or unprotected > 0:
            status = "warn"
            parts = []
            parts.append(
                f"Total guardrails: {len(guardrails)}, Published: {len(published)}."
            )
            if missing_filters:
                parts.append(f"Missing filters: {'; '.join(missing_filters[:5])}")
                if len(missing_filters) > 5:
                    parts.append(f"(+{len(missing_filters) - 5} more)")
            if draft_only > 0:
                parts.append(
                    f"{draft_only} agent(s) have guardrails in draft state only."
                )
            if unprotected > 0:
                parts.append(f"{unprotected} agent(s) have no guardrail configured.")
            detail = " ".join(parts)
            rec_parts = []
            if missing_filters:
                rec_parts.append("Add missing filter types to published guardrails.")
            if draft_only > 0:
                rec_parts.append(
                    "Publish agent versions to activate guardrail protection."
                )
            if unprotected > 0:
                rec_parts.append(
                    "Configure guardrails in unprotected agents orchestration settings."
                )
            recommendation = " ".join(rec_parts)
        else:
            status = "pass"
            detail = (
                f"Total guardrails: {len(guardrails)}, Published: {len(published)}. "
                f"All published guardrails have full filter coverage. "
                f"All {total_agents} agent(s) have published guardrails."
            )
            recommendation = None

        # Truncate detail and recommendation to 1024 chars
        if len(detail) > 1024:
            detail = detail[:1021] + "..."
        if recommendation and len(recommendation) > 1024:
            recommendation = recommendation[:1021] + "..."

        return {
            "check_name": "guardrails",
            "status": status,
            "detail": detail,
            "recommendation": recommendation,
            "data": {
                "guardrail_count": len(guardrails),
                "published_count": len(published),
                "guardrail_details": guardrail_details,
                "missing_filters": missing_filters,
                "total_agents": total_agents,
                "agents_with_guardrail_published": pub_protected,
                "agents_with_guardrail_draft_only": draft_only,
                "agents_without_guardrail": unprotected,
                "unprotected_agents": agents_without_guardrail,
            },
        }
    except TimeBudgetExceeded:
        raise
    except Exception as e:
        logger.error(f"[S1] Error: {e}")
        error_detail = f"Error checking guardrails: {str(e)}"
        if len(error_detail) > 1024:
            error_detail = error_detail[:1021] + "..."
        return {
            "check_name": "guardrails",
            "status": "error",
            "detail": error_detail,
            "recommendation": None,
            "data": None,
        }


def check_q_domain_encryption(qconnect_client, assistant_id, start_time, time_budget):
    """Check Q Connect domain encryption configuration.

    Uses the GetAssistant API to determine whether a customer-managed KMS key
    is configured for the Amazon Connect AI Agents domain.

    Args:
        qconnect_client: boto3 QConnect client.
        assistant_id: The Q Connect assistant ID.
        start_time: Epoch timestamp when execution began.
        time_budget: Allowed time budget in seconds.

    Returns:
        dict: Structured finding with check_name, status, detail, recommendation, data.
    """
    logger.info(
        f"[S2] Starting Domain Encryption check for assistant_id={assistant_id}"
    )
    try:
        check_time_budget(start_time, time_budget)
        response = qconnect_client.get_assistant(assistantId=assistant_id)
        assistant = response.get("assistant", {})
        logger.info(
            f"[S2] get_assistant returned: name={assistant.get('name', 'N/A')}, "
            f"status={assistant.get('status', 'N/A')}, type={assistant.get('type', 'N/A')}"
        )

        enc_config = assistant.get("serverSideEncryptionConfiguration", {})
        kms_key = enc_config.get("kmsKeyId", "")

        if kms_key:
            # Mask the key for display (keep first 40 chars if longer)
            masked_key = kms_key[:40] + "..." if len(kms_key) > 40 else kms_key
            detail = f"Customer-managed KMS key configured: {masked_key}"
            return {
                "check_name": "domain_encryption",
                "status": "pass",
                "detail": detail,
                "recommendation": None,
                "data": {"has_cmk": True, "kms_key_id": masked_key},
            }
        else:
            detail = "Using AWS-owned encryption key (no customer-managed KMS key)."
            recommendation = (
                "Configure a customer-managed KMS key for the Amazon Connect AI Agents domain "
                "to enable key rotation auditing and access control."
            )
            return {
                "check_name": "domain_encryption",
                "status": "warn",
                "detail": detail,
                "recommendation": recommendation,
                "data": {"has_cmk": False, "kms_key_id": None},
            }
    except TimeBudgetExceeded:
        raise
    except Exception as e:
        logger.error(f"[S2] Error: {e}")
        error_detail = f"Error checking domain encryption: {str(e)}"
        if len(error_detail) > 1024:
            error_detail = error_detail[:1021] + "..."
        return {
            "check_name": "domain_encryption",
            "status": "error",
            "detail": error_detail,
            "recommendation": None,
            "data": None,
        }


def check_ai_agent_logging(
    logs_client, assistant_id, account_id, aws_region, start_time, time_budget
):
    """Check AI agent logging configuration via CloudWatch delivery destinations.

    Args:
        logs_client: boto3 CloudWatch Logs client.
        assistant_id: The Q Connect assistant ID.
        account_id: The AWS account ID.
        aws_region: The AWS region.
        start_time: Epoch timestamp when execution began.
        time_budget: Allowed time budget in seconds.

    Returns:
        dict: Structured finding with check_name, status, detail, recommendation, data.
    """
    logger.info(
        f"[O5] Starting AI Agent Logging check for assistant_id={assistant_id}, account={account_id}, region={aws_region}"
    )
    try:
        assistant_arn = (
            f"arn:aws:wisdom:{aws_region}:{account_id}:assistant/{assistant_id}"
        )
        logger.info(f"[O5] Constructed assistant ARN: {assistant_arn}")

        check_time_budget(start_time, time_budget)
        response = logs_client.describe_deliveries()
        deliveries = response.get("deliveries", [])
        logger.info(
            f"[O5] describe_deliveries returned {len(deliveries)} total delivery/deliveries"
        )

        # Match by checking if assistant_id appears in source name OR by checking delivery source ARNs
        matching = []
        for d in deliveries:
            source_name = d.get("deliverySourceName", "")
            # Check direct assistant_id match in source name
            if assistant_id in source_name:
                matching.append(d)
            # Also check for wisdom/qconnect delivery sources (WdDeliverySource pattern)
            elif (
                "WdDeliverySource" in source_name
                or "wisdom" in source_name.lower()
                or "qconnect" in source_name.lower()
            ):
                try:
                    check_time_budget(start_time, time_budget)
                    source_detail = logs_client.describe_delivery_sources()
                    for src in source_detail.get("deliverySources", []):
                        if src.get("name") == source_name and assistant_id in src.get(
                            "resourceArn", ""
                        ):
                            matching.append(d)
                            logger.info(
                                f"[O5] Matched delivery via source ARN: {src.get('resourceArn')}"
                            )
                            break
                except TimeBudgetExceeded:
                    raise
                except Exception as src_err:
                    logger.debug(
                        f"[O5] Could not check delivery source detail: {src_err}"
                    )

        logger.info(
            f"[O5] Found {len(matching)} delivery/deliveries matching assistant_id={assistant_id}"
        )

        if matching:
            delivery_info = []
            for d in matching:
                delivery_info.append(
                    {
                        "id": d.get("id", "N/A"),
                        "source": d.get("deliverySourceName", "N/A"),
                        "destination_type": d.get("deliveryDestinationType", "N/A"),
                    }
                )
            detail = f"CloudWatch log delivery configured ({len(matching)} delivery/deliveries found)."
            if len(detail) > 1024:
                detail = detail[:1021] + "..."
            return {
                "check_name": "agent_logging",
                "status": "pass",
                "detail": detail,
                "recommendation": None,
                "data": {
                    "deliveries_found": len(matching),
                    "deliveries": delivery_info,
                    "assistant_arn": assistant_arn,
                },
            }
        else:
            detail = (
                "No CloudWatch Vended Logs delivery configured for AI Agent event logs. "
                "This checks for AI Agent-specific event log delivery "
                "(TRANSCRIPT_AGENTIC_MESSAGE, TRANSCRIPT_SELF_SERVICE_MESSAGE, etc.), "
                "not standard Connect contact flow logging which may be enabled separately "
                "at the instance level."
            )
            recommendation = (
                "Configure CloudWatch Vended Logs delivery for your AI Agent to capture "
                "event logs including TRANSCRIPT_AGENTIC_MESSAGE and TRANSCRIPT_SELF_SERVICE_MESSAGE. "
                "Additionally, use the ListSpans API as a starting point for debugging AI agent interactions."
            )
            if len(detail) > 1024:
                detail = detail[:1021] + "..."
            return {
                "check_name": "agent_logging",
                "status": "warn",
                "detail": detail,
                "recommendation": recommendation,
                "data": {
                    "deliveries_found": 0,
                    "deliveries": [],
                    "assistant_arn": assistant_arn,
                },
            }
    except TimeBudgetExceeded:
        raise
    except Exception as e:
        logger.error(f"[O5] Error: {e}")
        error_detail = f"Error checking AI agent logging: {str(e)}"
        if len(error_detail) > 1024:
            error_detail = error_detail[:1021] + "..."
        return {
            "check_name": "agent_logging",
            "status": "error",
            "detail": error_detail,
            "recommendation": None,
            "data": None,
        }


# ---------------------------------------------------------------------------
# Lambda Handler
# ---------------------------------------------------------------------------


def lambda_handler(event, context):
    """AI Analyzer Lambda handler.

    Validates input using shared validate_input, discovers the Q Connect
    assistant, runs AI-specific checks with graceful timeout, persists
    results to S3 Hive-style path, and returns a standardized AnalyzerResult.

    Args:
        event (dict): Input event with reviewId, instanceId, instanceArn,
                      accountId, awsRegion, daysBack, componentType,
                      s3ReportingBucket, and optional maxSeconds.
        context: Lambda context object.

    Returns:
        dict: Standardized AnalyzerResult (success or error).
    """
    logger.info("AI Analyzer invoked")
    start_time = time.time()
    component_type = event.get("componentType", "ai")

    # 1. Validate required input fields using shared utility
    try:
        validate_input(event)
    except ValueError as e:
        logger.error(str(e))
        return error_result(component_type, str(e))

    # 2. Extract fields from validated event
    instance_id = event["instanceId"]
    account_id = event["accountId"]
    aws_region = event["awsRegion"]

    # 3. Compute time budget
    max_seconds = event.get("maxSeconds", DEFAULT_MAX_SECONDS)
    time_budget = compute_time_budget(max_seconds)

    logger.info(
        f"Analyzing AI features for instance={instance_id}, "
        f"account={account_id}, region={aws_region}, "
        f"max_seconds={max_seconds}, time_budget={time_budget}"
    )

    # 4. Initialize pillar-keyed findings structure
    findings = {
        "security": [],
        "operational_excellence": [],
        "resilience": [],
        "observability": [],
        "metadata": {
            "assistant_id": None,
            "checks_completed": [],
            "timed_out": False,
            "account_id": account_id,
            "region": aws_region,
        },
    }

    # 5. Create boto3 clients
    connect_client = boto3.client("connect", region_name=aws_region)
    qconnect_client = boto3.client("qconnect", region_name=aws_region)
    logs_client = boto3.client("logs", region_name=aws_region)

    try:
        # 6. Discover assistant ID (gate for all subsequent checks)
        assistant_id = discover_assistant_id(
            connect_client, instance_id, start_time, time_budget, event=event
        )
        findings["metadata"]["assistant_id"] = assistant_id

        # Short-circuit if no assistant found
        if assistant_id is None:
            logger.info(f"No Q Connect assistant found for instance={instance_id}")
            findings["operational_excellence"].append(
                {
                    "check_name": "assistant_discovery",
                    "status": "info",
                    "detail": "No Amazon Q in Connect assistant is configured for this instance.",
                    "recommendation": "Consider enabling Amazon Q in Connect to leverage AI-powered agent assistance.",
                    "data": None,
                }
            )
            findings["metadata"]["checks_completed"].append("assistant_discovery")

            # Persist and return success
            try:
                s3_key = persist_to_s3(event, findings)
            except Exception as e:
                logger.error(f"Failed to persist AI findings to S3: {e}")
                return error_result(component_type, f"S3 persistence failed: {str(e)}")

            duration_ms = int((time.time() - start_time) * 1000)
            return success_result(
                component_type,
                findings,
                duration_ms=duration_ms,
                s3_key=s3_key,
            )

        logger.info(f"Discovered assistant_id={assistant_id}")
        findings["metadata"]["checks_completed"].append("assistant_discovery")

        # 7. Run checks sequentially with per-check error isolation

        # Check 1: AI Agent Inventory → operational_excellence
        # Also captures the agents list for reuse in guardrails check
        cached_agents = None
        try:
            check_time_budget(start_time, time_budget)
            result = check_ai_agent_inventory(
                qconnect_client,
                connect_client,
                assistant_id,
                instance_id,
                start_time,
                time_budget,
            )
            # Cache the raw agents count for guardrails cross-reference
            cached_agents = (
                result.get("data", {}).get("_raw_agents")
                if result.get("data")
                else None
            )
            findings["operational_excellence"].append(result)
            findings["metadata"]["checks_completed"].append("agent_inventory")
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("AccessDeniedException", "ResourceNotFoundException"):
                logger.warning(f"agent_inventory skipped due to {error_code}: {e}")
                findings["operational_excellence"].append(
                    {
                        "check_name": "agent_inventory",
                        "status": "info",
                        "detail": f"Check skipped: {error_code}",
                        "recommendation": None,
                        "data": {"error_code": error_code},
                    }
                )
            else:
                raise
        except TimeBudgetExceeded:
            raise
        except Exception as e:
            logger.error(f"Unexpected error in agent_inventory: {e}")
            findings["operational_excellence"].append(
                {
                    "check_name": "agent_inventory",
                    "status": "error",
                    "detail": f"Check failed: {str(e)[:1024]}",
                    "recommendation": None,
                    "data": None,
                }
            )

        # Check 2: AI Prompt Configuration → operational_excellence
        try:
            check_time_budget(start_time, time_budget)
            result = check_ai_prompt_configuration(
                qconnect_client,
                assistant_id,
                start_time,
                time_budget,
            )
            findings["operational_excellence"].append(result)
            findings["metadata"]["checks_completed"].append("prompt_configuration")
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("AccessDeniedException", "ResourceNotFoundException"):
                logger.warning(f"prompt_configuration skipped due to {error_code}: {e}")
                findings["operational_excellence"].append(
                    {
                        "check_name": "prompt_configuration",
                        "status": "info",
                        "detail": f"Check skipped: {error_code}",
                        "recommendation": None,
                        "data": {"error_code": error_code},
                    }
                )
            else:
                raise
        except TimeBudgetExceeded:
            raise
        except Exception as e:
            logger.error(f"Unexpected error in prompt_configuration: {e}")
            findings["operational_excellence"].append(
                {
                    "check_name": "prompt_configuration",
                    "status": "error",
                    "detail": f"Check failed: {str(e)[:1024]}",
                    "recommendation": None,
                    "data": None,
                }
            )

        # Check 3: Q Guardrails → security
        try:
            check_time_budget(start_time, time_budget)
            result = check_q_guardrails(
                qconnect_client,
                assistant_id,
                start_time,
                time_budget,
                cached_agents=cached_agents,
            )
            findings["security"].append(result)
            findings["metadata"]["checks_completed"].append("guardrails")
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("AccessDeniedException", "ResourceNotFoundException"):
                logger.warning(f"guardrails skipped due to {error_code}: {e}")
                findings["security"].append(
                    {
                        "check_name": "guardrails",
                        "status": "info",
                        "detail": f"Check skipped: {error_code}",
                        "recommendation": None,
                        "data": {"error_code": error_code},
                    }
                )
            else:
                raise
        except TimeBudgetExceeded:
            raise
        except Exception as e:
            logger.error(f"Unexpected error in guardrails: {e}")
            findings["security"].append(
                {
                    "check_name": "guardrails",
                    "status": "error",
                    "detail": f"Check failed: {str(e)[:1024]}",
                    "recommendation": None,
                    "data": None,
                }
            )

        # Check 4: Q Domain Encryption → security
        try:
            check_time_budget(start_time, time_budget)
            result = check_q_domain_encryption(
                qconnect_client,
                assistant_id,
                start_time,
                time_budget,
            )
            findings["security"].append(result)
            findings["metadata"]["checks_completed"].append("domain_encryption")
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("AccessDeniedException", "ResourceNotFoundException"):
                logger.warning(f"domain_encryption skipped due to {error_code}: {e}")
                findings["security"].append(
                    {
                        "check_name": "domain_encryption",
                        "status": "info",
                        "detail": f"Check skipped: {error_code}",
                        "recommendation": None,
                        "data": {"error_code": error_code},
                    }
                )
            else:
                raise
        except TimeBudgetExceeded:
            raise
        except Exception as e:
            logger.error(f"Unexpected error in domain_encryption: {e}")
            findings["security"].append(
                {
                    "check_name": "domain_encryption",
                    "status": "error",
                    "detail": f"Check failed: {str(e)[:1024]}",
                    "recommendation": None,
                    "data": None,
                }
            )

        # Check 5: AI Agent Logging → observability
        try:
            check_time_budget(start_time, time_budget)
            result = check_ai_agent_logging(
                logs_client,
                assistant_id,
                account_id,
                aws_region,
                start_time,
                time_budget,
            )
            findings["observability"].append(result)
            findings["metadata"]["checks_completed"].append("agent_logging")
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("AccessDeniedException", "ResourceNotFoundException"):
                logger.warning(f"agent_logging skipped due to {error_code}: {e}")
                findings["observability"].append(
                    {
                        "check_name": "agent_logging",
                        "status": "info",
                        "detail": f"Check skipped: {error_code}",
                        "recommendation": None,
                        "data": {"error_code": error_code},
                    }
                )
            else:
                raise
        except TimeBudgetExceeded:
            raise
        except Exception as e:
            logger.error(f"Unexpected error in agent_logging: {e}")
            findings["observability"].append(
                {
                    "check_name": "agent_logging",
                    "status": "error",
                    "detail": f"Check failed: {str(e)[:1024]}",
                    "recommendation": None,
                    "data": None,
                }
            )

    except TimeBudgetExceeded:
        logger.info(
            f"AI Analyzer reached time budget after completing "
            f"{len(findings['metadata']['checks_completed'])} checks: "
            f"{findings['metadata']['checks_completed']}"
        )
        findings["metadata"]["timed_out"] = True

        collected_count = len(findings["metadata"]["checks_completed"])
        total_estimated = TOTAL_CHECKS + 1  # checks + assistant discovery

        # Persist partial results
        try:
            s3_key = persist_to_s3(
                event,
                findings,
                partial=True,
                collected_count=collected_count,
                total_estimated=total_estimated,
            )
        except Exception as e:
            logger.error(f"Failed to persist partial AI findings to S3: {e}")
            return error_result(component_type, f"S3 persistence failed: {str(e)}")

        duration_ms = int((time.time() - start_time) * 1000)
        return success_result(
            component_type,
            findings,
            duration_ms=duration_ms,
            s3_key=s3_key,
            partial=True,
            collected_count=collected_count,
            total_estimated=total_estimated,
        )

    # 8. Persist full results to S3
    try:
        s3_key = persist_to_s3(event, findings)
    except Exception as e:
        logger.error(f"Failed to persist AI findings to S3: {e}")
        return error_result(component_type, f"S3 persistence failed: {str(e)}")

    duration_ms = int((time.time() - start_time) * 1000)
    checks_completed = findings["metadata"]["checks_completed"]

    logger.info(
        f"AI analysis complete: {len(checks_completed)} checks completed in {duration_ms}ms"
    )

    return success_result(
        component_type,
        findings,
        duration_ms=duration_ms,
        s3_key=s3_key,
    )
