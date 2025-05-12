"""
Wookieepedia content filtering module.

This module provides filters to identify and exclude non-content pages like
redirects, disambiguation pages, stubs, and meta/utility pages from the 
XML processing pipeline.
"""

import re
from typing import Dict, Set, Optional, List, Tuple

class ContentFilter:
    """
    Filter for identifying and excluding non-content pages from processing.
    
    This class implements various filters to identify different types of
    non-content pages in the Wookieepedia XML dump, such as redirects,
    disambiguation pages, stub articles, and meta/utility pages.
    """
    
    def __init__(self):
        """Initialize the content filter with regex patterns."""
        # Redirect detection
        self.redirect_pattern = re.compile(r'^\s*#(?:REDIRECT|redirect)\s*\[\[(.*?)\]\]', re.IGNORECASE)
        
        # Disambiguation detection
        self.disambiguation_patterns = [
            re.compile(r'\{\{(disambig|disambiguation|dab)[|}]', re.IGNORECASE),
            re.compile(r'may refer to', re.IGNORECASE),
            re.compile(r'disambiguation page', re.IGNORECASE)
        ]
        
        # Stub article detection
        self.stub_patterns = [
            re.compile(r'\{\{(stub|sectionstub|expansion|expand)[|}]', re.IGNORECASE),
            re.compile(r'\{\{(.*?-stub)[|}]', re.IGNORECASE),
        ]
        
        # Meta/utility page detection - only explicit maintenance templates
        self.meta_utility_patterns = [
            re.compile(r'\{\{(cleanup|delete|speedy|copyvio|copypaste)[|}]', re.IGNORECASE),
            re.compile(r'\{\{(merge|split)[|}]', re.IGNORECASE),
            re.compile(r'^__(NOTOC|NOEDITSECTION|FORCETOC|NEWSECTIONLINK)__', re.MULTILINE)
        ]
        
        # Important templates that shouldn't count towards template ratio
        self.important_templates = {
            'canon', 'legends', 'infobox', 'quote', 'citation', 'cite',
            'reference', 'c', 'character', 'era', 'faction', 'location',
            'planet', 'species', 'vehicle', 'weapon', 'organization', 
            'class', 'appearance', 'featured', 'film', 'media', 'eras'
        }
        
        # Content thresholds - significantly relaxed thresholds
        self.min_content_length = 150  # Further reduced from 200
        self.template_ratio_threshold = 0.35  # Increased from 0.25 (35%)
        
        # Content quality indicators
        self.reference_pattern = re.compile(r'<ref>.*?</ref>|{{cite', re.IGNORECASE)
        self.section_pattern = re.compile(r'^==([^=].*?)==\s*$', re.MULTILINE)
        self.infobox_pattern = re.compile(r'\{\{[Ii]nfobox\s+\w+', re.IGNORECASE)
        
        # Canon/Legends patterns - expanded matching patterns
        self.canon_pattern = re.compile(
            r'(?:\{\{[Cc]anon\}\}|\[\[Category:[Cc]anon articles\]\]|\[\[Category:.*?[Cc]anon\]\]|'
            r'\{\{[Cc]anonicity\|[Cc]anon\}\}|\{\{[Cc]anon-article\}\}|\{\{[Cc]anon\s*[|}]|'
            r'\{\{[Cc]anon-only\}\}|\{\{[Cc]anon-content\}\}|\{\{[Cc]anon-work\}\})',
            re.IGNORECASE
        )
        
        self.legends_pattern = re.compile(
            r'(?:\{\{[Ll]egends\}\}|\[\[Category:[Ll]egends articles\]\]|\[\[Category:.*?[Ll]egends\]\]|'
            r'\{\{[Ll]egends-article\}\}|\{\{[Ll]egends\s*[|}]|\{\{[Ll]egends-only\}\}|'
            r'\{\{[Ll]egends-content\}\}|\{\{[Ll]egends-work\}\}|\{\{[Ss]tar [Ww]ars [Ll]egends\}\})',
            re.IGNORECASE
        )
        
        self.era_pattern = re.compile(r'\{\{[Ee]ra\|[^}]+\}\}')
        
        # Quality article detection
        self.quality_article_patterns = [
            re.compile(r'\[\[Category:Wookieepedia Featured articles\]\]', re.IGNORECASE),
            re.compile(r'\[\[Category:Wookieepedia Good articles\]\]', re.IGNORECASE)
        ]
        
        # Quality template patterns - expanded list
        self.quality_templates = [
            re.compile(r'\{\{[Cc]anon\}\}'),
            re.compile(r'\{\{[Ll]egends\}\}'),
            re.compile(r'\{\{[Ee]ra\|[^}]+\}\}'),
            re.compile(r'\{\{[Ff]action\|[^}]+\}\}'),
            re.compile(r'\{\{[Cc]ite\|[^}]+\}\}'),
            re.compile(r'\{\{[Rr]eference\|[^}]+\}\}'),
            re.compile(r'\{\{[Qq]uote\|[^}]+\}\}'),
            re.compile(r'\{\{[Ii]mage\|[^}]+\}\}'),
            re.compile(r'\{\{[Aa]ttack\|[^}]+\}\}'),
            re.compile(r'\{\{[Cc]haracter\|[^}]+\}\}'),
            re.compile(r'\{\{[Hh]omepage\}\}'),
            re.compile(r'\{\{[Cc]haracters\|[^}]+\}\}'),
            re.compile(r'\{\{[Ee]vents\|[^}]+\}\}'),
            re.compile(r'\{\{[Cc]reatures\|[^}]+\}\}'),
            re.compile(r'\{\{[Ll]ocations\|[^}]+\}\}'),
            re.compile(r'\{\{[Oo]rganizations\|[^}]+\}\}')
        ]
    
    def is_redirect(self, content: str) -> bool:
        """
        Check if content is a redirect page.
        
        Args:
            content: Page content
            
        Returns:
            bool: True if the page is a redirect
        """
        return bool(self.redirect_pattern.match(content))
    
    def is_disambiguation(self, title: str, content: str) -> bool:
        """
        Check if content is a disambiguation page.
        
        Args:
            title: Page title
            content: Page content
            
        Returns:
            bool: True if the page is a disambiguation page
        """
        # Check for "disambiguation" in title
        if re.search(r'\(disambiguation\)', title, re.IGNORECASE):
            return True
            
        # Check for disambiguation templates or phrases
        for pattern in self.disambiguation_patterns:
            if pattern.search(content):
                return True
                
        return False
    
    def _count_important_templates(self, content: str) -> int:
        """Count occurrences of important templates that shouldn't count towards template ratio."""
        count = 0
        for template in self.important_templates:
            count += len(re.findall(r'\{\{' + template, content, re.IGNORECASE))
        return count
    
    def _count_quality_templates(self, content: str) -> int:
        """Count occurrences of templates that indicate article quality."""
        count = 0
        for pattern in self.quality_templates:
            if pattern.search(content):
                count += 1
        return count
    
    def is_meta_utility(self, title: str, content: str) -> bool:
        """
        Check if content is a meta or utility page.
        
        Args:
            title: Page title
            content: Page content
            
        Returns:
            bool: True if the page is a meta/utility page
        """
        # Check for explicit maintenance templates
        for pattern in self.meta_utility_patterns:
            if pattern.search(content):
                return True
                
        # Check for templates that make up most of the page
        total_templates = len(re.findall(r'\{\{', content))
        important_templates = self._count_important_templates(content)
        content_length = len(content)
        
        if content_length > 0:
            # Only count non-important templates towards ratio
            template_ratio = (total_templates - important_templates) / content_length
            if template_ratio > self.template_ratio_threshold:
                return True
                
        return False
    
    def _get_clean_content(self, content: str) -> str:
        """Get clean content without templates and categories."""
        clean = re.sub(r'\{\{[^}]*\}\}', '', content)
        clean = re.sub(r'\[\[Category:.*?\]\]', '', clean)
        clean = re.sub(r'\[\[(.*?)\]\]', r'\1', clean)
        clean = re.sub(r"''(.*?)''", r'\1', clean)
        return clean.strip()
    
    def has_quality_indicators(self, content: str) -> bool:
        """Check for indicators of article quality."""
        # Check if it's a featured or good article
        for pattern in self.quality_article_patterns:
            if pattern.search(content):
                return True
        
        # Check for references
        if self.reference_pattern.search(content):
            return True
            
        # Check for multiple sections
        sections = self.section_pattern.findall(content)
        if len(sections) >= 2:  # At least 2 sections indicates structure
            return True
            
        # Check for multiple quality templates
        quality_count = self._count_quality_templates(content)
        if quality_count >= 2:
            return True
            
        # Check for important Star Wars content markers with sufficient content
        if (self.canon_pattern.search(content) or self.legends_pattern.search(content) or self.era_pattern.search(content)):
            clean_content = self._get_clean_content(content)
            if len(clean_content) > 50:  # Lower threshold for marker + content
                return True
            
        # Check for infobox with content
        if self.infobox_pattern.search(content):
            clean_content = self._get_clean_content(content)
            if len(clean_content) > 30:  # Very low threshold for infobox + content
                return True
            
        return False
    
    def is_canon_or_legends(self, content: str, categories: Optional[Set[str]] = None) -> Optional[bool]:
        """
        Determine if content is Canon, Legends, or unknown.
        
        Args:
            content: Page content
            categories: Optional set of extracted categories
            
        Returns:
            Optional[bool]: True for Canon, False for Legends, None if unknown
        """
        # Look for explicit Canon template at top of article
        if re.search(r'\{\{Canon\}\}|\{\{Canon article\}\}|\[\[Category:Canon articles\]\]', content, re.IGNORECASE):
            return True
            
        # Look for explicit Legends template at top of article
        if re.search(r'\{\{Legends\}\}|\{\{Legends article\}\}|\[\[Category:Legends articles\]\]', content, re.IGNORECASE):
            return False
        
        # Cannot determine
        return None
    
    def is_stub(self, content: str, plain_text: Optional[str] = None) -> bool:
        """
        Check if content is a stub article.
        
        Args:
            content: Page content (wikitext)
            plain_text: Optional pre-processed plain text version
            
        Returns:
            bool: True if the page is a stub article
        """
        # Extract any categories for additional analysis
        categories = set()
        category_pattern = r'\[\[Category:([^\]|]+)(?:\|[^\]]+)?\]\]'
        matches = re.finditer(category_pattern, content, re.IGNORECASE)
        for match in matches:
            categories.add(match.group(1).strip().lower())
        
        # Check if it's a featured or good article - these are NEVER stubs
        for pattern in self.quality_article_patterns:
            if pattern.search(content):
                return False
        
        # Determine content length
        if plain_text:
            text_length = len(plain_text.strip())
        else:
            text_length = len(self._get_clean_content(content))
        
        # Check for explicit stub templates but ONLY consider it a stub
        # if the content is actually short (handles outdated stub templates)
        has_stub_template = False
        for pattern in self.stub_patterns:
            if pattern.search(content):
                has_stub_template = True
                break
        
        # If it has a stub template but is long enough, ignore the template
        if has_stub_template and text_length >= 500:
            has_stub_template = False
        
        # If it has quality indicators, it's not a stub
        if self.has_quality_indicators(content):
            return False
        
        # Check Canon/Legends status
        is_canon = self.is_canon_or_legends(content, categories)
            
        # For Canon articles, be more lenient
        if is_canon is True:
            # Canon article needs either:
            # 1. Decent length (>100 chars)
            # 2. Multiple quality templates
            if text_length > 100 or self._count_quality_templates(content) >= 1:
                return False
            
            # Only consider it a stub if it has an explicit stub template AND is short
            return has_stub_template and text_length < 100
        
        # For Legends articles, also be more lenient
        if is_canon is False:
            if text_length > 120 or self._count_quality_templates(content) >= 1:
                return False
            
            # Only consider it a stub if it has an explicit stub template OR is very short
            return has_stub_template or text_length < 100
                
        # For articles with infobox, significantly lower the bar
        if self.infobox_pattern.search(content):
            return text_length < 80  # Very low threshold for articles with infobox
        
        # For multi-section articles, lower the threshold
        if len(self.section_pattern.findall(content)) >= 2:
            return text_length < 100
            
        # For articles with quality templates, lower the threshold
        if self._count_quality_templates(content) >= 1:
            return text_length < 120
            
        # Check for stub template last - at this point, only consider it a stub
        # if it has an explicit stub template AND is below the minimum content length
        if has_stub_template and text_length < self.min_content_length:
            return True
            
        # Final length check - much stricter for content without quality indicators
        return text_length < self.min_content_length
    
    def should_process(self, title: str, content: str, plain_text: Optional[str] = None) -> Tuple[bool, str]:
        """
        Determine if content should be processed based on all filters.
        
        Args:
            title: Page title
            content: Page content (wikitext)
            plain_text: Optional pre-processed plain text
            
        Returns:
            Tuple[bool, str]: (should_process, reason)
        """
        # Check for redirects
        if self.is_redirect(content):
            return False, "redirect"
            
        # Check for disambiguation pages
        if self.is_disambiguation(title, content):
            return False, "disambiguation"
            
        # Check for meta/utility pages - do this before stub check
        if self.is_meta_utility(title, content):
            return False, "meta_utility"
            
        # No longer filtering out stubs - all non-redirect, non-disambiguation, 
        # non-meta/utility pages will be processed
        return True, "content"
    
    def get_filter_stats(self, titles: List[str], contents: List[str], 
                        plain_texts: Optional[List[str]] = None) -> Dict[str, int]:
        """
        Get statistics about content filtering for a batch of pages.
        
        Args:
            titles: List of page titles
            contents: List of page contents
            plain_texts: Optional list of plain texts
            
        Returns:
            Dict[str, int]: Filter statistics
        """
        stats = {
            "total": len(titles),
            "redirects": 0,
            "disambiguation": 0,
            "stubs": 0,
            "meta_utility": 0,
            "content": 0
        }
        
        if plain_texts is None:
            plain_texts = [None] * len(titles)
            
        for i, (title, content) in enumerate(zip(titles, contents)):
            plain_text = plain_texts[i] if i < len(plain_texts) else None
            
            should_process, reason = self.should_process(title, content, plain_text)
            
            if reason == "redirect":
                stats["redirects"] += 1
            elif reason == "disambiguation":
                stats["disambiguation"] += 1
            elif reason == "stub":
                stats["stubs"] += 1
            elif reason == "meta_utility":
                stats["meta_utility"] += 1
            else:
                stats["content"] += 1
                
        return stats 