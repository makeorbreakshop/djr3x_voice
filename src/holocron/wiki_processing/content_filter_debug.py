"""
Debug version of Wookieepedia content filtering module.

This module extends the ContentFilter class to provide detailed debug information
about why pages are being classified as stubs or other filtered types.
"""

import re
from typing import Dict, Set, Optional, List, Tuple, Any

from holocron.wiki_processing.content_filter import ContentFilter

class ContentFilterDebug(ContentFilter):
    """
    Debug version of ContentFilter that provides detailed reasoning.
    
    This class extends the ContentFilter to provide debug information about
    why pages are being classified as stubs or other filtered types.
    """
    
    def __init__(self):
        """Initialize the debug content filter."""
        super().__init__()
        
    def is_stub_debug(self, content: str, plain_text: Optional[str] = None) -> Tuple[bool, Dict[str, Any]]:
        """
        Check if content is a stub article with debug information.
        
        Args:
            content: Page content
            plain_text: Optional pre-processed plain text
            
        Returns:
            Tuple[bool, dict]: (is_stub, debug_info)
        """
        debug_info = {
            "text_length": 0,
            "min_content_length": self.min_content_length,
            "has_explicit_stub_template": False,
            "has_references": False,
            "has_multiple_sections": False,
            "has_multiple_quality_templates": False,
            "has_canon_or_era_marker": False,
            "has_infobox": False,
            "quality_template_count": 0,
            "section_count": 0,
            "failed_checks": [],
            "passed_checks": []
        }
        
        # Get text length
        if plain_text:
            text_length = len(plain_text.strip())
        else:
            text_length = len(self._get_clean_content(content))
        
        debug_info["text_length"] = text_length
        
        # Check for explicit stub template
        for pattern in self.stub_patterns:
            if pattern.search(content):
                debug_info["has_explicit_stub_template"] = True
                debug_info["failed_checks"].append("Has explicit stub template")
                return True, debug_info
        
        # Check for content quality indicators that would exempt from stub status
        
        # Check for references
        if self.reference_pattern.search(content):
            debug_info["has_references"] = True
            debug_info["passed_checks"].append("Has references")
            return False, debug_info
            
        # Check for multiple sections
        sections = self.section_pattern.findall(content)
        debug_info["section_count"] = len(sections)
        
        if len(sections) >= 2:
            debug_info["has_multiple_sections"] = True
            debug_info["passed_checks"].append(f"Has multiple sections ({len(sections)})")
            return False, debug_info
        
        # Check for canonical/era content with sufficient length
        has_canon = bool(self.canon_pattern.search(content))
        has_era = bool(self.era_pattern.search(content))
        
        if (has_canon or has_era):
            debug_info["has_canon_or_era_marker"] = True
            clean_content = self._get_clean_content(content)
            if len(clean_content) > 50:  # Lower threshold for marker + content
                debug_info["passed_checks"].append(f"Has canon/era marker with sufficient content ({len(clean_content)} chars)")
                return False, debug_info
            else:
                debug_info["failed_checks"].append(f"Has canon/era marker but insufficient content ({len(clean_content)} chars < 50)")
        
        # Check for infobox with minimal content
        has_infobox = bool(self.infobox_pattern.search(content))
        debug_info["has_infobox"] = has_infobox
        
        if has_infobox:
            if plain_text:
                if len(plain_text.strip()) > 30:  # Very low threshold for infobox + any content
                    debug_info["passed_checks"].append(f"Has infobox with sufficient content ({len(plain_text.strip())} chars)")
                    return False, debug_info
                else:
                    debug_info["failed_checks"].append(f"Has infobox but insufficient content ({len(plain_text.strip())} chars < 30)")
            else:
                clean_content = self._get_clean_content(content)
                if len(clean_content) > 30:  # Very low threshold for infobox + any content
                    debug_info["passed_checks"].append(f"Has infobox with sufficient content ({len(clean_content)} chars)")
                    return False, debug_info
                else:
                    debug_info["failed_checks"].append(f"Has infobox but insufficient content ({len(clean_content)} chars < 30)")
        
        # Check for multiple quality templates
        quality_template_count = self._count_quality_templates(content)
        debug_info["quality_template_count"] = quality_template_count
        
        if quality_template_count >= 2 and text_length > 30:
            debug_info["has_multiple_quality_templates"] = True
            debug_info["passed_checks"].append(f"Has multiple quality templates ({quality_template_count}) with sufficient content ({text_length} chars)")
            return False, debug_info
        elif quality_template_count >= 2:
            debug_info["has_multiple_quality_templates"] = True
            debug_info["failed_checks"].append(f"Has multiple quality templates ({quality_template_count}) but insufficient content ({text_length} chars < 30)")
        
        # Final length check
        if text_length < self.min_content_length:
            debug_info["failed_checks"].append(f"Content too short ({text_length} chars < {self.min_content_length})")
            return True, debug_info
        else:
            debug_info["passed_checks"].append(f"Content length sufficient ({text_length} chars >= {self.min_content_length})")
            return False, debug_info
    
    def should_process_debug(self, title: str, content: str, plain_text: Optional[str] = None) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Determine if content should be processed with debug information.
        
        Args:
            title: Page title
            content: Page content (wikitext)
            plain_text: Optional pre-processed plain text
            
        Returns:
            Tuple[bool, str, dict]: (should_process, reason, debug_info)
        """
        debug_info = {}
        
        # Check for redirects
        if self.is_redirect(content):
            return False, "redirect", {"reason": "redirect"}
            
        # Check for disambiguation pages
        if self.is_disambiguation(title, content):
            return False, "disambiguation", {"reason": "disambiguation"}
            
        # Check for meta/utility pages - do this before stub check
        if self.is_meta_utility(title, content):
            return False, "meta_utility", {"reason": "meta_utility"}
            
        # Check for stub articles with debug info
        is_stub, stub_debug_info = self.is_stub_debug(content, plain_text)
        if is_stub:
            return False, "stub", {
                "reason": "stub", 
                "stub_details": stub_debug_info
            }
            
        # Passed all filters - this is real content
        return True, "content", {"reason": "content"} 