#!/bin/bash

# Ad-hoc Amazon Connect Operational Review Runner
# This script provides easy ways to trigger reviews on-demand

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Default values
STACK_NAME="amazon-connect-ops-review"
DAYS_BACK=7

# Function to get the orchestrator function name
get_function_name() {
    aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`OrchestratorFunctionArn`].OutputValue' \
        --output text 2>/dev/null | awk -F: '{print $NF}'
}

# Function to invoke the review
invoke_review() {
    local days=$1
    local function_name=$(get_function_name)
    
    if [ -z "$function_name" ]; then
        echo -e "${YELLOW}Error: Could not find orchestrator function${NC}"
        echo "Make sure the stack '$STACK_NAME' is deployed"
        exit 1
    fi
    
    echo -e "${BLUE}Invoking review with daysBack=$days${NC}"
    echo -e "${BLUE}Function: $function_name${NC}\n"
    
    aws lambda invoke \
        --function-name "$function_name" \
        --payload "{\"daysBack\": $days}" \
        --cli-binary-format raw-in-base64-out \
        response.json
    
    echo -e "\n${GREEN}Response:${NC}"
    cat response.json | jq '.'
    
    # Extract report URL if available
    REPORT_URL=$(cat response.json | jq -r '.body' | jq -r '.reportUrl' 2>/dev/null)
    if [ ! -z "$REPORT_URL" ] && [ "$REPORT_URL" != "null" ]; then
        echo -e "\n${GREEN}Report URL:${NC} $REPORT_URL"
    fi
    
    rm response.json
}

# Function to show logs
show_logs() {
    local function_name=$(get_function_name)
    echo -e "${BLUE}Tailing logs for $function_name...${NC}"
    sam logs --name OrchestratorFunction --stack-name "$STACK_NAME" --tail
}

# Function to check status
check_status() {
    local function_name=$(get_function_name)
    echo -e "${BLUE}Checking function status...${NC}\n"
    
    aws lambda get-function \
        --function-name "$function_name" \
        --query '{Name:Configuration.FunctionName,State:Configuration.State,LastModified:Configuration.LastModified,Runtime:Configuration.Runtime,Timeout:Configuration.Timeout}' \
        --output table
}

# Function to list recent reports
list_reports() {
    local bucket=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`ReportBucket`].OutputValue' \
        --output text)
    
    echo -e "${BLUE}Recent reports in s3://$bucket:${NC}\n"
    aws s3 ls "s3://$bucket/" --recursive | grep "\.html$" | tail -10
}

# Main menu
show_menu() {
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}Amazon Connect Ops Review - Ad-hoc Runner${NC}"
    echo -e "${GREEN}========================================${NC}\n"
    echo "1. Quick test (last 24 hours)"
    echo "2. Standard review (last 7 days)"
    echo "3. Full review (last 30 days)"
    echo "4. Custom days back"
    echo "5. View logs"
    echo "6. Check function status"
    echo "7. List recent reports"
    echo "8. Exit"
    echo ""
}

# Parse command line arguments
while getopts "d:s:h" opt; do
    case $opt in
        d)
            DAYS_BACK=$OPTARG
            invoke_review "$DAYS_BACK"
            exit 0
            ;;
        s)
            STACK_NAME=$OPTARG
            ;;
        h)
            echo "Usage: $0 [-d days_back] [-s stack_name]"
            echo ""
            echo "Options:"
            echo "  -d    Number of days to analyze (default: 7)"
            echo "  -s    Stack name (default: amazon-connect-ops-review)"
            echo "  -h    Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0 -d 14              # Run review for last 14 days"
            echo "  $0 -d 30 -s my-stack  # Run 30-day review on custom stack"
            echo "  $0                    # Interactive menu"
            exit 0
            ;;
        \?)
            echo "Invalid option: -$OPTARG" >&2
            exit 1
            ;;
    esac
done

# Interactive mode
while true; do
    show_menu
    read -p "Select option (1-8): " choice
    
    case $choice in
        1)
            echo ""
            invoke_review 1
            echo ""
            read -p "Press Enter to continue..."
            ;;
        2)
            echo ""
            invoke_review 7
            echo ""
            read -p "Press Enter to continue..."
            ;;
        3)
            echo ""
            invoke_review 30
            echo ""
            read -p "Press Enter to continue..."
            ;;
        4)
            echo ""
            read -p "Enter number of days to analyze: " custom_days
            invoke_review "$custom_days"
            echo ""
            read -p "Press Enter to continue..."
            ;;
        5)
            echo ""
            show_logs
            ;;
        6)
            echo ""
            check_status
            echo ""
            read -p "Press Enter to continue..."
            ;;
        7)
            echo ""
            list_reports
            echo ""
            read -p "Press Enter to continue..."
            ;;
        8)
            echo -e "${YELLOW}Exiting...${NC}"
            exit 0
            ;;
        *)
            echo -e "${YELLOW}Invalid option${NC}"
            sleep 1
            ;;
    esac
done
