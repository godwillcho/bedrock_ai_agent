"""
Amazon Connect MCP Tool Manager

Create, convert, or delete flow modules as MCP tools.

Usage:
    # Create with default (empty return) content
    python mcp_tool_manager.py create-module --instance-id <ID> --module-name MyTool

    # Create with custom content from a JSON file
    python mcp_tool_manager.py create-module --instance-id <ID> --module-name MyTool --content-file flow.json

    # Convert an existing flow module to an MCP tool
    python mcp_tool_manager.py convert-module --instance-id <ID> --module-id <ID>
    python mcp_tool_manager.py convert-module --instance-id <ID> --module-id <ID> --content-file flow.json

    # Delete a flow module
    python mcp_tool_manager.py delete-module --instance-id <ID> --module-id <ID>

Environment variables:
    CONNECT_INSTANCE_ID   Amazon Connect instance ID
    AWS_REGION            AWS region (default: us-west-2)
"""

import argparse
import json
import logging
import os
import uuid

import boto3

logging.basicConfig(format="%(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


# ===========================================================================
# Flow Module Content
# ===========================================================================

def build_default_content() -> str:
    """Build minimal flow module content with just a return block."""
    end_id = str(uuid.uuid4())
    content = {
        "Version": "2019-10-30",
        "StartAction": end_id,
        "Settings": {
            "InputParameters": [],
            "OutputParameters": [],
            "Transitions": [
                {"DisplayName": "Success", "ReferenceName": "Success", "Description": ""},
                {"DisplayName": "Error", "ReferenceName": "Error", "Description": ""},
            ],
        },
        "Actions": [
            {"Identifier": end_id, "Type": "EndFlowModuleExecution", "Parameters": {}, "Transitions": {}},
        ],
    }
    return json.dumps(content)


def load_content(content_file: str) -> str:
    """Load module content from a JSON file and return as a JSON string."""
    with open(content_file, "r") as f:
        content = json.load(f)
    return json.dumps(content)


# ===========================================================================
# Connect client
# ===========================================================================

def get_connect_client(region: str):
    return boto3.Session(region_name=region).client("connect")


# ===========================================================================
# CLI commands
# ===========================================================================

def cmd_create_module(args):
    connect = get_connect_client(args.region)
    name = args.module_name
    content = load_content(args.content_file) if args.content_file else build_default_content()

    resp = connect.create_contact_flow_module(
        InstanceId=args.instance_id,
        Name=name,
        Description=args.description or "",
        Content=content,
        ExternalInvocationConfiguration={"Enabled": True},
    )
    module_id = resp["Id"]

    # Publish the module so it appears in Security Profiles
    connect.create_contact_flow_module_version(
        InstanceId=args.instance_id, ContactFlowModuleId=module_id,
    )
    logger.info("Created and published flow module: %s (ID: %s)", name, module_id)


def cmd_convert_module(args):
    connect = get_connect_client(args.region)

    # Fetch existing module details
    module = connect.describe_contact_flow_module(
        InstanceId=args.instance_id, ContactFlowModuleId=args.module_id,
    )["ContactFlowModule"]
    old_name = module["Name"]
    description = args.description if args.description is not None else module.get("Description", "")
    content = load_content(args.content_file) if args.content_file else build_default_content()

    # Delete the old module — ExternalInvocationConfiguration can only be set at creation time
    connect.delete_contact_flow_module(
        InstanceId=args.instance_id, ContactFlowModuleId=args.module_id,
    )
    logger.info("Deleted old module '%s' (%s)", old_name, args.module_id)

    # Recreate with ExternalInvocationConfiguration enabled
    name = args.module_name or old_name
    resp = connect.create_contact_flow_module(
        InstanceId=args.instance_id,
        Name=name,
        Description=description,
        Content=content,
        ExternalInvocationConfiguration={"Enabled": True},
    )
    new_id = resp["Id"]

    # Publish the module so it appears in Security Profiles
    connect.create_contact_flow_module_version(
        InstanceId=args.instance_id, ContactFlowModuleId=new_id,
    )
    logger.info("Recreated and published as MCP tool: %s (new ID: %s)", name, new_id)


def cmd_deploy_tools(args):
    connect = get_connect_client(args.region)
    tools_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")

    if not os.path.isdir(tools_dir):
        logger.error("tools/ folder not found at %s", tools_dir)
        return

    json_files = sorted(f for f in os.listdir(tools_dir) if f.endswith(".json"))
    if not json_files:
        logger.info("No JSON files found in tools/ folder")
        return

    logger.info("Found %d tool(s) in tools/ folder\n", len(json_files))

    for filename in json_files:
        # Filename becomes the module name: e.g. "AccountBalance.json" -> "AccountBalance-Tool"
        tool_name = os.path.splitext(filename)[0]
        module_name = f"{tool_name}-Tool"
        filepath = os.path.join(tools_dir, filename)

        try:
            content = load_content(filepath)
            resp = connect.create_contact_flow_module(
                InstanceId=args.instance_id,
                Name=module_name,
                Description=args.description or "",
                Content=content,
                ExternalInvocationConfiguration={"Enabled": True},
            )
            module_id = resp["Id"]
            connect.create_contact_flow_module_version(
                InstanceId=args.instance_id, ContactFlowModuleId=module_id,
            )
            logger.info("  Created: %s (ID: %s)", module_name, module_id)
        except Exception as e:
            logger.error("  Failed: %s — %s", module_name, e)

    logger.info("\nDone.")


def cmd_delete_module(args):
    connect = get_connect_client(args.region)
    module = connect.describe_contact_flow_module(
        InstanceId=args.instance_id, ContactFlowModuleId=args.module_id,
    )["ContactFlowModule"]
    connect.delete_contact_flow_module(
        InstanceId=args.instance_id, ContactFlowModuleId=args.module_id,
    )
    logger.info("Deleted flow module: %s (%s)", module["Name"], args.module_id)


def cmd_delete_tools(args):
    connect = get_connect_client(args.region)
    delete_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "delete")

    if not os.path.isdir(delete_dir):
        logger.error("delete/ folder not found at %s", delete_dir)
        return

    # Collect all module names from JSON files in the delete/ folder
    names_to_delete = []
    json_files = sorted(f for f in os.listdir(delete_dir) if f.endswith(".json"))
    if not json_files:
        logger.info("No JSON files found in delete/ folder")
        return

    for filename in json_files:
        filepath = os.path.join(delete_dir, filename)
        with open(filepath, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            names_to_delete.extend(data.keys())
        elif isinstance(data, list):
            names_to_delete.extend(data)
        else:
            logger.error("Skipping %s — must contain a JSON object or list", filename)

    if not names_to_delete:
        logger.info("No module names found to delete")
        return

    # List all modules and build a name -> id lookup
    modules = []
    resp = connect.list_contact_flow_modules(InstanceId=args.instance_id, MaxResults=50)
    modules.extend(resp.get("ContactFlowModulesSummaryList", []))
    while resp.get("NextToken"):
        resp = connect.list_contact_flow_modules(InstanceId=args.instance_id, MaxResults=50, NextToken=resp["NextToken"])
        modules.extend(resp.get("ContactFlowModulesSummaryList", []))

    name_to_id = {m["Name"]: m["Id"] for m in modules}

    logger.info("Deleting %d module(s)\n", len(names_to_delete))

    for name in names_to_delete:
        module_id = name_to_id.get(name)
        if not module_id:
            logger.error("  Not found: %s", name)
            continue
        try:
            connect.delete_contact_flow_module(
                InstanceId=args.instance_id, ContactFlowModuleId=module_id,
            )
            logger.info("  Deleted: %s (%s)", name, module_id)
        except Exception as e:
            logger.error("  Failed: %s — %s", name, e)


# ===========================================================================
# CLI entry point
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Amazon Connect MCP Tool Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create with default (empty return) content
  python mcp_tool_manager.py create-module --instance-id abc123 --module-name MyTool

  # Create with custom content from a JSON file
  python mcp_tool_manager.py create-module --instance-id abc123 --module-name MyTool \\
      --content-file flow.json

  # Convert an existing flow module to an MCP tool
  python mcp_tool_manager.py convert-module --instance-id abc123 --module-id mod-456

  # Deploy all tools from the tools/ folder
  python mcp_tool_manager.py deploy-tools --instance-id abc123

  # Delete a single flow module
  python mcp_tool_manager.py delete-module --instance-id abc123 --module-id mod-456

  # Delete modules listed in JSON files inside the delete/ folder
  # delete/batch1.json: ["CheckBalance-Tool", "OrderLookup-Tool"]
  python mcp_tool_manager.py delete-tools --instance-id abc123
        """,
    )
    parser.add_argument("--instance-id", default=None, help="Connect instance ID (or set CONNECT_INSTANCE_ID)")
    parser.add_argument("--region", default="us-west-2")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-module", help="Create a new MCP tool flow module")
    p.add_argument("--module-name", required=True, help="Name for the flow module")
    p.add_argument("--content-file", default=None, help="JSON file with module content (default: empty return block)")
    p.add_argument("--description", default="")

    p = sub.add_parser("convert-module", help="Convert existing flow module to MCP tool (deletes and recreates)")
    p.add_argument("--module-id", required=True)
    p.add_argument("--content-file", default=None, help="JSON file with module content (default: empty return block)")
    p.add_argument("--description", default=None)
    p.add_argument("--module-name", default=None, help="New name (defaults to original name)")

    p = sub.add_parser("deploy-tools", help="Create MCP tools from all JSON files in the tools/ folder")
    p.add_argument("--description", default="")

    p = sub.add_parser("delete-module", help="Delete a single flow module")
    p.add_argument("--module-id", required=True)

    sub.add_parser("delete-tools", help="Delete modules listed in JSON files inside the delete/ folder")

    args = parser.parse_args()

    if not args.instance_id:
        args.instance_id = os.environ.get("CONNECT_INSTANCE_ID")
    if not args.instance_id:
        parser.error("--instance-id is required (or set CONNECT_INSTANCE_ID)")

    {"create-module": cmd_create_module, "convert-module": cmd_convert_module, "deploy-tools": cmd_deploy_tools, "delete-module": cmd_delete_module, "delete-tools": cmd_delete_tools}[args.command](args)


if __name__ == "__main__":
    main()
