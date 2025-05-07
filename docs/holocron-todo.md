# Holocron RAG System Implementation TODO

## 📋 URL Collection Tasks

### Using Existing URL Collection System
- [ ] Configure sitemap_crawler.py for targeted content discovery
- [ ] Update category_crawler.py to focus on specific Phase 1 categories
- [ ] Create priority metadata in url_store.py for different phases
- [ ] Generate reports with reporting.py to track content coverage progress

### For Phase 1 - Core Identity (50-100 articles)
- [ ] Collect URLs for R3X/RX-24 specific information
  - [ ] Use category_crawler for "Category:Droids" with RX name filter
  - [ ] Use sitemap_crawler with search terms related to Star Tours
- [ ] Collect URLs for Oga's Cantina and direct workplace
  - [ ] Use category_crawler for "Category:Locations" with Batuu/Galaxy's Edge filters
  - [ ] Use sitemap_crawler with "Oga's Cantina" and related terms
- [ ] Collect URLs for DJ/entertainment roles in Star Wars
  - [ ] Use category_crawler for music/entertainment categories
  - [ ] Use sitemap_crawler with terms related to music and entertainment

### For Phase 2 - Immediate Context (200-300 articles)
- [ ] Collect URLs for Batuu and Black Spire Outpost
  - [ ] Use category_crawler for related location categories
  - [ ] Use sitemap_crawler with Batuu-related search terms
- [ ] Collect URLs for Galaxy's Edge locations and characters
  - [ ] Define specific category queries for Galaxy's Edge content
  - [ ] Use targeted search terms in sitemap_crawler
- [ ] Collect URLs for Star Tours history and locations
  - [ ] Define specific category queries for Star Tours content
  - [ ] Use targeted search terms in sitemap_crawler

### For Phase 3 - Professional Context (500-1000 articles)
- [ ] Collect URLs for Droids (especially RX-series and entertainment models)
- [ ] Collect URLs for Music and musicians in Star Wars
- [ ] Collect URLs for Spaceports and cantinas
- [ ] Collect URLs for Piloting and navigation

### For Phase 4 - General Knowledge
- [ ] Collect URLs for Major characters, events, and locations
- [ ] Prioritize based on relevance to typical DJ R3X conversations

## 📋 Phased Content Approach

### Phase 1 - Core Identity (50-100 articles)
- [ ] Process and embed R3X/RX-24 specific information
- [ ] Process and embed Oga's Cantina and direct workplace
- [ ] Process and embed DJ/entertainment roles in Star Wars

### Phase 2 - Immediate Context (200-300 articles)
- [ ] Process and embed Batuu and Black Spire Outpost
- [ ] Process and embed Galaxy's Edge locations and characters
- [ ] Process and embed Star Tours history and locations

### Phase 3 - Professional Context (500-1000 articles)
- [ ] Process and embed Droids (especially RX-series and entertainment models)
- [ ] Process and embed Music and musicians in Star Wars
- [ ] Process and embed Spaceports and cantinas
- [ ] Process and embed Piloting and navigation

### Phase 4 - General Knowledge
- [ ] Process and embed Major characters, events, and locations
- [ ] Process and embed content prioritized by relevance to typical DJ R3X conversations

## 🔄 Batch Processing System

- [ ] Enhance scraper to work with batched URL lists
- [ ] Implement queue-based worker system for parallel processing
- [ ] Add progress tracking and resumability features
- [ ] Implement polite crawling with rate limiting
- [ ] Add detailed logging and error recovery
- [ ] Optimize database operations for bulk insertions

## 🗃️ Database Optimizations

- [ ] Implement bulk vector insertion for better performance
- [ ] Add connection pooling enhancements for parallel operations
- [ ] Implement retry logic with exponential backoff
- [ ] Create monitoring for query performance as the database grows
- [ ] Adjust index parameters if needed for larger collections

## 🧪 Testing and Validation

- [ ] Create benchmark suite for testing retrieval performance
- [ ] Implement validation for knowledge quality and relevance
- [ ] Test with increasingly large knowledge bases
- [ ] Measure and optimize query latency
- [ ] Create automated reporting on knowledge base health

## 🔄 Future Work

### ROS Migration
- [ ] Refactor Holocron Manager to use ROS instead of current event bus
- [ ] Create ROS nodes for knowledge retrieval and processing
- [ ] Implement proper message passing between components
- [ ] Ensure backward compatibility with existing data format

### Performance Optimization
- [ ] Measure and optimize response latency
- [ ] Implement caching for common queries

### Visual Integration
- [ ] Add LED patterns/animations for "consulting the holocron"
- [ ] Provide visual feedback during knowledge retrieval

## 📊 Progress Tracking

| Component | Target | Current | % Complete |
|-----------|--------|---------|------------|
| Phase 1   | 100    | 0       | 0%         |
| Phase 2   | 300    | 0       | 0%         |
| Phase 3   | 1000   | 0       | 0%         |
| Phase 4   | TBD    | 0       | 0%         |

## 🧰 Next Steps

1. Configure existing URL collection system for Phase 1 content
2. Collect and process Phase 1 articles
3. Set up basic batch processing capabilities
4. Create progress monitoring system

## 📆 Weekly Priorities

- Week 1: Configure URL collection and complete Phase 1 article collection
- Week 2: Implement basic batch processing and begin Phase 2
- Week 3: Improve database optimizations and continue Phase 2
- Week 4: Begin testing and validation framework
