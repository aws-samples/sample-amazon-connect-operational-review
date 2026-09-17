#!/usr/bin/env python3
"""
Ad-hoc Amazon Connect Operational Review Runner
Python version for cross-platform compatibility
"""

import boto3
import json
import sys
import argparse

# Initialize AWS clients
lambda_client = boto3.client("lambda")
cfn_client = boto3.client("cloudformation")
s3_client = boto3.client("s3")


def get_function_name(stack_name):
    """Get the orchestrator function name from CloudFormation stack"""
    try:
        response = cfn_client.describe_stacks(StackName=stack_name)
        outputs = response["Stacks"][0]["Outputs"]

        for output in outputs:
            if output["OutputKey"] == "OrchestratorFunctionArn":
                return output["OutputValue"].split(":")[-1]

        print(f"Error: Could not find OrchestratorFunctionArn in stack {stack_name}")
        return None
    except Exception as e:
        print(f"Error getting function name: {e}")
        return None


def get_bucket_name(stack_name):
    """Get the S3 bucket name from CloudFormation stack"""
    try:
        response = cfn_client.describe_stacks(StackName=stack_name)
        outputs = response["Stacks"][0]["Outputs"]

        for output in outputs:
            if output["OutputKey"] == "ReportBucket":
                return output["OutputValue"]

        return None
    except Exception as e:
        print(f"Error getting bucket name: {e}")
        return None


def invoke_review(function_name, days_back=7):
    """Invoke the review Lambda function"""
    print(f"\n🚀 Invoking review with daysBack={days_back}")
    print(f"Function: {function_name}\n")

    payload = {"daysBack": days_back}

    try:
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload),
        )

        result = json.loads(response["Payload"].read())

        print("✅ Response:")
        print(json.dumps(result, indent=2))

        # Extract report URL if available
        if "body" in result:
            body = json.loads(result["body"])
            if "reportUrl" in body:
                print(f"\n📊 Report URL: {body['reportUrl']}")
            if "reviewId" in body:
                print(f"📝 Review ID: {body['reviewId']}")

        return result

    except Exception as e:
        print(f"❌ Error invoking function: {e}")
        return None


def list_recent_reports(bucket_name, limit=10):
    """List recent reports from S3 bucket"""
    print(f"\n📁 Recent reports in s3://{bucket_name}:\n")

    try:
        response = s3_client.list_objects_v2(
            Bucket=bucket_name, Prefix="connect-ops-review-"
        )

        if "Contents" not in response:
            print("No reports found")
            return

        # Sort by last modified
        objects = sorted(
            response["Contents"], key=lambda x: x["LastModified"], reverse=True
        )[:limit]

        for obj in objects:
            size_kb = obj["Size"] / 1024
            print(f"  {obj['Key']}")
            print(f"    Size: {size_kb:.1f} KB")
            print(f"    Modified: {obj['LastModified']}")
            print(f"    URL: https://{bucket_name}.s3.amazonaws.com/{obj['Key']}\n")

    except Exception as e:
        print(f"Error listing reports: {e}")


def check_function_status(function_name):
    """Check Lambda function status"""
    print("\n🔍 Checking function status...\n")

    try:
        response = lambda_client.get_function(FunctionName=function_name)
        config = response["Configuration"]

        print(f"Function Name: {config['FunctionName']}")
        print(f"State: {config['State']}")
        print(f"Runtime: {config['Runtime']}")
        print(f"Timeout: {config['Timeout']} seconds")
        print(f"Memory: {config['MemorySize']} MB")
        print(f"Last Modified: {config['LastModified']}")

    except Exception as e:
        print(f"Error checking status: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Run Amazon Connect Operational Review ad-hoc",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --days 7                    # Run review for last 7 days
  %(prog)s --days 30 --stack my-stack  # Run 30-day review on custom stack
  %(prog)s --list-reports              # List recent reports
  %(prog)s --status                    # Check function status
        """,
    )

    parser.add_argument(
        "-d", "--days", type=int, help="Number of days to analyze (default: 7)"
    )

    parser.add_argument(
        "-s",
        "--stack",
        default="amazon-connect-ops-review",
        help="CloudFormation stack name (default: amazon-connect-ops-review)",
    )

    parser.add_argument(
        "--list-reports", action="store_true", help="List recent reports from S3"
    )

    parser.add_argument(
        "--status", action="store_true", help="Check Lambda function status"
    )

    parser.add_argument(
        "--quick", action="store_true", help="Quick test (last 24 hours)"
    )

    parser.add_argument(
        "--full", action="store_true", help="Full review (last 30 days)"
    )

    args = parser.parse_args()

    # Get function name
    function_name = get_function_name(args.stack)
    if not function_name:
        sys.exit(1)

    # Handle different actions
    if args.list_reports:
        bucket_name = get_bucket_name(args.stack)
        if bucket_name:
            list_recent_reports(bucket_name)
        else:
            print("Error: Could not find S3 bucket")
            sys.exit(1)

    elif args.status:
        check_function_status(function_name)

    elif args.quick:
        invoke_review(function_name, days_back=1)

    elif args.full:
        invoke_review(function_name, days_back=30)

    elif args.days:
        invoke_review(function_name, days_back=args.days)

    else:
        # Interactive mode
        print("\n" + "=" * 50)
        print("Amazon Connect Ops Review - Ad-hoc Runner")
        print("=" * 50)
        print("\n1. Quick test (last 24 hours)")
        print("2. Standard review (last 7 days)")
        print("3. Full review (last 30 days)")
        print("4. Custom days back")
        print("5. List recent reports")
        print("6. Check function status")
        print("7. Exit")

        choice = input("\nSelect option (1-7): ")

        if choice == "1":
            invoke_review(function_name, days_back=1)
        elif choice == "2":
            invoke_review(function_name, days_back=7)
        elif choice == "3":
            invoke_review(function_name, days_back=30)
        elif choice == "4":
            days = int(input("Enter number of days to analyze: "))
            invoke_review(function_name, days_back=days)
        elif choice == "5":
            bucket_name = get_bucket_name(args.stack)
            if bucket_name:
                list_recent_reports(bucket_name)
        elif choice == "6":
            check_function_status(function_name)
        elif choice == "7":
            print("Exiting...")
            sys.exit(0)
        else:
            print("Invalid option")
            sys.exit(1)


if __name__ == "__main__":
    main()
