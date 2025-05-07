"""
Reporting module for analyzing URL collection progress and content coverage.
"""

import logging
from typing import Dict, List, Optional
import pandas as pd
from datetime import datetime, timedelta
from supabase import create_client, Client
from dotenv import load_dotenv
import os
import json

logger = logging.getLogger(__name__)

class CoverageReporter:
    """Generates reports and analytics for URL collection progress."""
    
    def __init__(self):
        load_dotenv()
        
        # Initialize Supabase client
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        
        if not supabase_url or not supabase_key:
            raise ValueError("Missing Supabase credentials in environment")
            
        self.supabase: Client = create_client(supabase_url, supabase_key)
        
    async def get_collection_stats(self) -> Dict[str, int]:
        """Get basic statistics about collected URLs."""
        try:
            # Using raw SQL for complex aggregations
            stats_query = """
            SELECT
                COUNT(*) as total_urls,
                COUNT(*) FILTER (WHERE content_type = 'canonical') as canonical_urls,
                COUNT(*) FILTER (WHERE content_type = 'legends') as legends_urls,
                COUNT(*) FILTER (WHERE content_type = 'unknown') as unknown_urls,
                COUNT(*) FILTER (WHERE is_processed = true) as processed_urls,
                COUNT(DISTINCT UNNEST(categories)) as unique_categories
            FROM holocron_urls;
            """
            
            result = await self.supabase.postgrest.rpc('exec', {'query': stats_query}).execute()
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Error fetching collection stats: {e}")
            return {}
            
    async def get_category_coverage(self) -> Dict[str, Dict[str, int]]:
        """Analyze URL coverage by category."""
        try:
            coverage_query = """
            WITH RECURSIVE category_stats AS (
                SELECT 
                    category,
                    COUNT(*) as url_count,
                    COUNT(*) FILTER (WHERE content_type = 'canonical') as canonical_count,
                    COUNT(*) FILTER (WHERE is_processed = true) as processed_count
                FROM holocron_urls,
                     UNNEST(categories) as category
                GROUP BY category
            )
            SELECT * FROM category_stats
            ORDER BY url_count DESC;
            """
            
            result = await self.supabase.postgrest.rpc('exec', {'query': coverage_query}).execute()
            
            coverage = {}
            for row in result.data:
                coverage[row['category']] = {
                    'total_urls': row['url_count'],
                    'canonical_urls': row['canonical_count'],
                    'processed_urls': row['processed_count']
                }
                
            return coverage
        except Exception as e:
            logger.error(f"Error fetching category coverage: {e}")
            return {}
            
    async def get_priority_distribution(self) -> Dict[str, Dict[str, int]]:
        """Analyze URL distribution by priority level."""
        try:
            priority_query = """
            SELECT 
                priority,
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE is_processed = true) as processed,
                COUNT(*) FILTER (WHERE content_type = 'canonical') as canonical
            FROM holocron_urls
            GROUP BY priority;
            """
            
            result = await self.supabase.postgrest.rpc('exec', {'query': priority_query}).execute()
            
            distribution = {}
            for row in result.data:
                distribution[row['priority']] = {
                    'total': row['total'],
                    'processed': row['processed'],
                    'canonical': row['canonical']
                }
                
            return distribution
        except Exception as e:
            logger.error(f"Error fetching priority distribution: {e}")
            return {}
            
    async def get_collection_progress(self, days: int = 7) -> List[Dict]:
        """Get URL collection progress over time."""
        try:
            progress_query = f"""
            WITH daily_stats AS (
                SELECT 
                    DATE_TRUNC('day', discovered_at) as date,
                    COUNT(*) as new_urls,
                    COUNT(*) FILTER (WHERE content_type = 'canonical') as new_canonical,
                    COUNT(*) FILTER (WHERE is_processed = true) as processed
                FROM holocron_urls
                WHERE discovered_at >= NOW() - INTERVAL '{days} days'
                GROUP BY DATE_TRUNC('day', discovered_at)
                ORDER BY date
            )
            SELECT * FROM daily_stats;
            """
            
            result = await self.supabase.postgrest.rpc('exec', {'query': progress_query}).execute()
            return result.data if result.data else []
        except Exception as e:
            logger.error(f"Error fetching collection progress: {e}")
            return []
            
    def generate_coverage_report(self, stats: Dict, 
                               category_coverage: Dict, 
                               priority_dist: Dict) -> str:
        """Generate a formatted coverage report."""
        report = []
        
        # Overall statistics
        report.append("📊 URL Collection Coverage Report")
        report.append("=" * 30)
        report.append(f"\n📈 Overall Statistics:")
        report.append(f"Total URLs: {stats.get('total_urls', 0):,}")
        report.append(f"Canonical Content: {stats.get('canonical_urls', 0):,}")
        report.append(f"Legends Content: {stats.get('legends_urls', 0):,}")
        report.append(f"Unknown Content: {stats.get('unknown_urls', 0):,}")
        report.append(f"Processed URLs: {stats.get('processed_urls', 0):,}")
        report.append(f"Unique Categories: {stats.get('unique_categories', 0):,}")
        
        # Priority distribution
        report.append("\n🎯 Priority Distribution:")
        for priority, counts in priority_dist.items():
            report.append(f"\n{priority.upper()}:")
            report.append(f"  Total: {counts['total']:,}")
            report.append(f"  Processed: {counts['processed']:,}")
            report.append(f"  Canonical: {counts['canonical']:,}")
            
        # Top categories
        report.append("\n📑 Top Categories by URL Count:")
        sorted_categories = sorted(
            category_coverage.items(),
            key=lambda x: x[1]['total_urls'],
            reverse=True
        )[:10]
        
        for category, stats in sorted_categories:
            report.append(f"\n{category}:")
            report.append(f"  Total URLs: {stats['total_urls']:,}")
            report.append(f"  Canonical: {stats['canonical_urls']:,}")
            report.append(f"  Processed: {stats['processed_urls']:,}")
            
        return "\n".join(report)
        
    async def save_report(self, report: str):
        """Save the coverage report to the database."""
        try:
            data = {
                'report_text': report,
                'generated_at': datetime.utcnow().isoformat(),
                'metadata': {'type': 'coverage_report'}
            }
            
            await self.supabase.table('holocron_reports').insert(data).execute()
        except Exception as e:
            logger.error(f"Error saving report: {e}")
            
    async def generate_full_report(self) -> str:
        """Generate and save a complete coverage report."""
        # Gather all statistics
        stats = await self.get_collection_stats()
        category_coverage = await self.get_category_coverage()
        priority_dist = await self.get_priority_distribution()
        
        # Generate report
        report = self.generate_coverage_report(
            stats,
            category_coverage,
            priority_dist
        )
        
        # Save report
        await self.save_report(report)
        
        return report 