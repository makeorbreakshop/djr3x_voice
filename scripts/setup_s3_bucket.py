#!/usr/bin/env python3
"""
S3 Bucket Setup Script for Pinecone Migration

This script sets up an S3 bucket for storing Parquet files during the Pinecone migration:
1. Creates the S3 bucket if it doesn't exist
2. Configures bucket policies for secure access
3. Sets up the namespace directory structure
4. Updates environment variables with bucket information

Usage:
    python scripts/setup_s3_bucket.py [--region REGION] [--bucket-name NAME]
"""

import os
import sys
import boto3
import logging
import argparse
from botocore.exceptions import ClientError
from typing import Dict, Any
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def create_bucket(bucket_name: str, region: str) -> bool:
    """
    Create an S3 bucket in the specified region.
    
    Args:
        bucket_name: Name of the bucket to create
        region: AWS region where the bucket should be created
        
    Returns:
        True if bucket was created or already exists, False on error
    """
    try:
        s3_client = boto3.client('s3', region_name=region)
        
        # Check if bucket already exists
        try:
            s3_client.head_bucket(Bucket=bucket_name)
            logger.info(f"Bucket {bucket_name} already exists")
            return True
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == '404':
                # Bucket doesn't exist, create it
                if region == 'us-east-1':
                    s3_client.create_bucket(Bucket=bucket_name)
                else:
                    s3_client.create_bucket(
                        Bucket=bucket_name,
                        CreateBucketConfiguration={'LocationConstraint': region}
                    )
                logger.info(f"Created bucket {bucket_name} in region {region}")
                return True
            else:
                logger.error(f"Error checking bucket existence: {str(e)}")
                return False
                
    except Exception as e:
        logger.error(f"Error creating bucket: {str(e)}")
        return False

def setup_bucket_policy(bucket_name: str) -> bool:
    """
    Set up the bucket policy for secure access.
    
    Args:
        bucket_name: Name of the bucket to configure
        
    Returns:
        True if policy was set successfully, False on error
    """
    try:
        s3_client = boto3.client('s3')
        
        # Create a bucket policy that allows access only from your application
        bucket_policy = {
            'Version': '2012-10-17',
            'Statement': [{
                'Sid': 'PineconeParquetAccess',
                'Effect': 'Allow',
                'Principal': {'AWS': '*'},  # Will be updated with specific IAM role
                'Action': ['s3:GetObject', 's3:PutObject', 's3:ListBucket'],
                'Resource': [
                    f'arn:aws:s3:::{bucket_name}',
                    f'arn:aws:s3:::{bucket_name}/*'
                ]
            }]
        }
        
        # Convert policy to JSON string
        import json
        bucket_policy_string = json.dumps(bucket_policy)
        
        # Set the policy
        s3_client.put_bucket_policy(
            Bucket=bucket_name,
            Policy=bucket_policy_string
        )
        
        logger.info(f"Set bucket policy for {bucket_name}")
        return True
        
    except Exception as e:
        logger.error(f"Error setting bucket policy: {str(e)}")
        return False

def create_namespace_structure(bucket_name: str, namespaces: list) -> bool:
    """
    Create the namespace directory structure in the bucket.
    
    Args:
        bucket_name: Name of the bucket
        namespaces: List of namespaces to create
        
    Returns:
        True if structure was created successfully, False on error
    """
    try:
        s3_client = boto3.client('s3')
        
        # Create a folder for each namespace
        for namespace in namespaces:
            key = f"{namespace}/"
            s3_client.put_object(
                Bucket=bucket_name,
                Key=key
            )
            logger.info(f"Created namespace directory: {key}")
            
        return True
        
    except Exception as e:
        logger.error(f"Error creating namespace structure: {str(e)}")
        return False

def update_env_file(bucket_name: str, region: str) -> bool:
    """
    Update the .env file with S3 bucket information.
    
    Args:
        bucket_name: Name of the bucket
        region: AWS region of the bucket
        
    Returns:
        True if .env was updated successfully, False on error
    """
    try:
        env_path = '.env'
        
        # Read existing .env content
        env_content = []
        if os.path.exists(env_path):
            with open(env_path, 'r') as f:
                env_content = f.readlines()
        
        # Remove any existing S3 config
        env_content = [line for line in env_content 
                      if not line.startswith(('S3_BUCKET=', 'S3_REGION='))]
        
        # Add new S3 config
        env_content.extend([
            f"\n# S3 Configuration for Pinecone Migration\n",
            f"S3_BUCKET={bucket_name}\n",
            f"S3_REGION={region}\n"
        ])
        
        # Write updated content
        with open(env_path, 'w') as f:
            f.writelines(env_content)
            
        logger.info("Updated .env file with S3 configuration")
        return True
        
    except Exception as e:
        logger.error(f"Error updating .env file: {str(e)}")
        return False

def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(description='Set up S3 bucket for Pinecone migration')
    parser.add_argument('--region', default='us-east-1',
                      help='AWS region for the bucket (default: us-east-1)')
    parser.add_argument('--bucket-name', default='holocron-pinecone-migration',
                      help='Name of the S3 bucket (default: holocron-pinecone-migration)')
    args = parser.parse_args()
    
    # Load environment variables
    load_dotenv()
    
    # Create and configure the bucket
    if not create_bucket(args.bucket_name, args.region):
        logger.error("Failed to create/verify bucket")
        sys.exit(1)
        
    if not setup_bucket_policy(args.bucket_name):
        logger.error("Failed to set bucket policy")
        sys.exit(1)
        
    # Create namespace structure
    namespaces = ['default', 'priority', 'test']
    if not create_namespace_structure(args.bucket_name, namespaces):
        logger.error("Failed to create namespace structure")
        sys.exit(1)
        
    # Update .env file
    if not update_env_file(args.bucket_name, args.region):
        logger.error("Failed to update .env file")
        sys.exit(1)
        
    logger.info("S3 bucket setup completed successfully")

if __name__ == '__main__':
    main() 