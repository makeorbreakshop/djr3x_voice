import pytest
from holocron.wiki_processing.content_filter import ContentFilter

@pytest.fixture
def content_filter():
    return ContentFilter()

def test_canon_article_handling(content_filter):
    """Test that Canon articles are properly handled."""
    # Canon article with decent content should be kept
    canon_article = """
{{Canon}}
{{Infobox character
|name=DJ-R3X
|image=DJ-R3X.jpg
|homeworld=Unknown
|creator=Unknown
|manufacturer=Unknown
|line=RX-series pilot droid
}}
DJ-R3X, also known as Rex, was a RX-series pilot droid who worked as a Star Tours pilot before becoming a DJ at Oga's Cantina.

==History==
Originally a Star Tours pilot.

==Behind the scenes==
DJ-R3X first appeared in Star Tours.

[[Category:Canon articles]]
"""
    assert not content_filter.is_stub(canon_article)
    assert content_filter.should_process("DJ-R3X", canon_article)[0]

    # Very short canon article with no structure should still be marked as stub
    short_canon = """
{{Canon}}
DJ-R3X was a droid.
[[Category:Canon articles]]
"""
    assert content_filter.is_stub(short_canon)

def test_template_heavy_article(content_filter):
    """Test that articles with many important templates aren't filtered out."""
    template_heavy = """
{{Canon}}
{{Infobox character}}
{{Quote|Some quote here|attr=Someone}}
{{Quote|Another quote|attr=Another}}
{{C|This is a comment}}
{{Citation needed}}
{{Reference|book}}
{{Era|Rise of the Empire}}
{{Faction|Rebel Alliance}}

Basic content about the character.
==History==
More content here.

[[Category:Canon articles]]
"""
    assert not content_filter.is_meta_utility("Test", template_heavy)
    assert content_filter.should_process("Test", template_heavy)[0]

def test_quality_indicators(content_filter):
    """Test detection of quality indicators."""
    article_with_refs = """
Some content here.
<ref>Source book, page 45</ref>
More content.
{{cite book|title=Star Wars Book}}
"""
    assert content_filter.has_quality_indicators(article_with_refs)

    article_with_sections = """
Intro text.
==History==
History content.
==Behind the scenes==
Production info.
"""
    assert content_filter.has_quality_indicators(article_with_sections)

    article_with_markers = """
{{Canon}}
{{Era|Rise of the Empire}}
Basic content.
"""
    assert content_filter.has_quality_indicators(article_with_markers)

def test_stub_detection(content_filter):
    """Test that stub detection is not too aggressive."""
    # Article with infobox and minimal content should not be stub
    infobox_article = """
{{Infobox character
|name=Character
|species=Human
}}
This character appeared in several episodes.
"""
    assert not content_filter.is_stub(infobox_article, "This character appeared in several episodes.")

    # Article with references should not be stub despite length
    short_with_refs = """
The ship was a CR90 corvette.<ref>Source book</ref>
It served in the Rebel Alliance.<ref>Another source</ref>
"""
    assert content_filter.has_quality_indicators(short_with_refs)
    assert not content_filter.is_stub(short_with_refs)

def test_meta_utility_detection(content_filter):
    """Test that meta/utility detection is not too aggressive."""
    # Article with maintenance template should be filtered
    maintenance_article = """
{{cleanup}}
Content here.
"""
    assert content_filter.is_meta_utility("Test", maintenance_article)

    # Article with many good templates should not be filtered
    good_templates = """
{{Canon}}
{{Infobox ship}}
{{Quote|Famous quote|attr=Captain}}
{{Era|Rebellion}}
Content about the ship.
"""
    assert not content_filter.is_meta_utility("Test", good_templates)

def test_real_world_examples(content_filter):
    """Test with real examples that were incorrectly filtered before."""
    # Example of a short but valid canon article
    droid_article = """
{{Canon}}
{{Infobox droid
|name=R-3X
|image=R-3X.jpg
|manufacturer=Unknown
|line=RX-series
}}
R-3X was an RX-series pilot droid that served as a pilot for Star Tours before being reprogrammed as a DJ for Oga's Cantina in Black Spire Outpost. The droid was known for its upbeat personality and music mixing skills.

==Behind the scenes==
R-3X was voiced by Paul Reubens.

[[Category:Canon articles]]
[[Category:RX-series pilot droids]]
"""
    assert not content_filter.is_stub(droid_article)
    assert content_filter.should_process("R-3X", droid_article)[0]

    # Example of a location article with mostly templates
    location_article = """
{{Canon}}
{{Infobox location
|name=Oga's Cantina
|image=Ogas_Cantina.jpg
|planet=Batuu
|region=Black Spire Outpost
|type=Cantina
}}
{{Quote|Best drinks in the galaxy!|Oga Garra}}
{{Quote|Welcome to Oga's!|DJ R-3X}}
Oga's Cantina was a cantina located in Black Spire Outpost on Batuu. It was owned by Oga Garra and featured DJ R-3X as its resident entertainer.

[[Category:Canon articles]]
[[Category:Cantinas]]
"""
    assert not content_filter.is_meta_utility("Oga's Cantina", location_article)
    assert not content_filter.is_stub(location_article)
    assert content_filter.should_process("Oga's Cantina", location_article)[0]

def test_filter_stats(content_filter):
    """Test that filter statistics are correctly calculated."""
    titles = ["Article 1", "Redirect", "Stub", "Good Article", "Disambiguation"]
    contents = [
        """{{Canon}}
        Good content with multiple sections.
        ==Section 1==
        Content.
        ==Section 2==
        More content.
        """,
        
        "#REDIRECT [[Other Article]]",
        
        """{{stub}}
        Very short content.
        """,
        
        """{{Canon}}
        {{Infobox character}}
        Good content with references.
        <ref>Source</ref>
        """,
        
        """This term may refer to:
        * Item 1
        * Item 2
        {{disambiguation}}
        """
    ]
    
    stats = content_filter.get_filter_stats(titles, contents)
    assert stats["total"] == 5
    assert stats["redirects"] == 1
    assert stats["stubs"] == 1
    assert stats["disambiguation"] == 1
    assert stats["content"] == 2  # Should have 2 good articles

def test_edge_cases(content_filter):
    """Test edge cases and boundary conditions."""
    # Empty content
    assert content_filter.is_stub("", "")
    
    # Content exactly at minimum length
    min_length_content = "a" * content_filter.min_content_length
    assert not content_filter.is_stub(min_length_content, min_length_content)
    
    # Content just below minimum length but with quality indicators
    short_but_good = f"""
{{Canon}}
{'a' * (content_filter.min_content_length - 50)}
<ref>Source</ref>
"""
    assert not content_filter.is_stub(short_but_good)
    
    # Article with exactly two sections (minimum for structure)
    two_sections = """
==Section 1==
Content
==Section 2==
Content
"""
    assert content_filter.has_quality_indicators(two_sections) 