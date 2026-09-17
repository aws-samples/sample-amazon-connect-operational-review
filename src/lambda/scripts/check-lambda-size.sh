#!/bin/bash
# Check Lambda code size against CloudFormation inline limit
# Exit with error if code exceeds 4KB limit

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
LAMBDA_FILE="${1:-lambda_function.py}"
INLINE_LIMIT=4194304  # 4 MB CloudFormation ZipFile limit

# Check if file exists
if [ ! -f "$LAMBDA_FILE" ]; then
    echo -e "${RED}❌ Error: File not found: $LAMBDA_FILE${NC}"
    exit 1
fi

# Get file size
SIZE=$(wc -c < "$LAMBDA_FILE")
SIZE_KB=$(echo "scale=2; $SIZE / 1024" | bc)
SIZE_MB=$(echo "scale=4; $SIZE / 1024 / 1024" | bc)
LIMIT_KB=$(echo "scale=2; $INLINE_LIMIT / 1024" | bc)
LIMIT_MB=$(echo "scale=2; $INLINE_LIMIT / 1024 / 1024" | bc)

echo -e "${BLUE}╔════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  Lambda Code Size Check                                ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "File: $LAMBDA_FILE"
echo "Size: $SIZE bytes ($SIZE_KB KB / $SIZE_MB MB)"
echo "CloudFormation ZipFile limit: $INLINE_LIMIT bytes ($LIMIT_KB KB / $LIMIT_MB MB)"
echo ""

if [ $SIZE -gt $INLINE_LIMIT ]; then
    OVER_BY=$((SIZE - INLINE_LIMIT))
    OVER_BY_KB=$(echo "scale=2; $OVER_BY / 1024" | bc)
    OVER_BY_MB=$(echo "scale=2; $OVER_BY / 1024 / 1024" | bc)
    RATIO=$(echo "scale=1; $SIZE / $INLINE_LIMIT" | bc)
    
    echo -e "${RED}❌ EXCEEDS LIMIT${NC}"
    echo ""
    echo "Your code is ${RATIO}x larger than the CloudFormation inline limit!"
    echo "Over by: $OVER_BY bytes ($OVER_BY_KB KB / $OVER_BY_MB MB)"
    echo ""
    echo -e "${YELLOW}⚠️  CloudFormation deployment will FAIL with inline code (ZipFile)${NC}"
    echo ""
    echo "You MUST use one of these deployment methods:"
    echo "  1. S3 deployment (recommended)"
    echo "     - See: scripts/deploy-lambda-s3.sh"
    echo "     - Template: CFT-AmazonConnectOperationsReview-S3.yml"
    echo ""
    echo "  2. Container deployment"
    echo "     - See: deployments/container/"
    echo ""
    echo "  3. AWS SAM"
    echo "     - See: deployments/sam/"
    echo ""
    echo "Documentation: docs/CLOUDFORMATION-SIZE-LIMITS.md"
    exit 1
else
    HEADROOM=$((INLINE_LIMIT - SIZE))
    HEADROOM_KB=$(echo "scale=2; $HEADROOM / 1024" | bc)
    HEADROOM_MB=$(echo "scale=2; $HEADROOM / 1024 / 1024" | bc)
    PERCENT_USED=$(echo "scale=1; ($SIZE * 100) / $INLINE_LIMIT" | bc)
    
    echo -e "${GREEN}✅ Within CloudFormation inline limit${NC}"
    echo ""
    echo "Headroom: $HEADROOM bytes ($HEADROOM_KB KB / $HEADROOM_MB MB)"
    echo "Usage: ${PERCENT_USED}% of limit"
    echo ""
    echo -e "${GREEN}✓ You can use inline code (ZipFile) in CloudFormation${NC}"
    echo ""
    
    if [ $SIZE -gt 3145728 ]; then  # 75% of 4MB
        echo -e "${YELLOW}⚠️  Warning: Approaching limit (>75%)${NC}"
        echo "Consider using S3 deployment for future growth"
    fi
    
    exit 0
fi
