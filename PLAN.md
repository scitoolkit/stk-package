# SciToolkit Implementation Plan

## Vision

SciToolkit is a community-driven platform for scientific agentic tools - making it as easy as possible for researchers to create, publish, share, search, download, and use AI tools for science.

## Project Information

- **Package Name**: scitoolkit (secured on PyPI)
- **Domain**: scitoolkit.org
- **GitHub Org**: https://github.com/scitoolkit
- **Email**: scitoolkit.dev@gmail.com
- **Author**: Alex Roman

### Repositories

1. **stk-website** (https://github.com/scitoolkit/stk-website.git)
   - Frontend website (Netlify deployment)
   - Browse/search UI, toolkit detail pages, documentation

2. **stk-backend** (planned)
   - API server for toolkit registry
   - Authentication, upload/download endpoints
   - Database management
   - **Hosted on Triton** (Alex's home server - Lenovo Mini PC, Windows 11)

3. **scitoolkit** (this repo - will move to org)
   - Python package/CLI tool
   - Core functionality for init, publish, install commands
   - Integration with Orchestral AI and MCP

### Infrastructure

- **Frontend**: Netlify (free tier)
- **Backend**: Triton home server (Lenovo Mini PC, Windows 11)
  - Running Python FastAPI application
  - SQLite or PostgreSQL database
  - Local file storage (or mount network storage)
  - Port forwarding for external access
  - Consider using ngrok or Cloudflare Tunnel for HTTPS
- **Domain**: scitoolkit.org (DNS pointing to backend)

## Architecture Overview

### Three Main Components

```
┌─────────────────────────────────────────────────────────────┐
│                     scitoolkit.org                           │
│  (Frontend - React/Next.js on Netlify)                      │
│  - Browse toolkits                                           │
│  - Search by domain/keyword                                  │
│  - User accounts & API key generation                        │
│  - Toolkit detail pages (README, metadata, downloads)        │
└─────────────────────────────────────────────────────────────┘
                            ▲
                            │ REST API
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    Backend API Server                        │
│  (Python FastAPI on Triton - Home Server)                   │
│  - /api/upload - Receive toolkit uploads                    │
│  - /api/download - Serve toolkit files                      │
│  - /api/registry - JSON registry index                      │
│  - /api/auth - API key management                           │
│  - PostgreSQL database (users, toolkits, downloads)         │
│  - S3/Cloud Storage for toolkit files                       │
└─────────────────────────────────────────────────────────────┘
                            ▲
                            │ HTTPS
                            ▼
┌─────────────────────────────────────────────────────────────┐
│              scitoolkit CLI (Python Package)                 │
│  Commands:                                                   │
│  - scitoolkit init       # Create toolkit template          │
│  - scitoolkit login      # Authenticate with API key        │
│  - scitoolkit publish    # Upload toolkit to registry       │
│  - scitoolkit search     # Search for toolkits              │
│  - scitoolkit install    # Download & setup toolkit         │
│  - scitoolkit list       # Show installed toolkits          │
│  - scitoolkit serve      # Start MCP server for toolkits    │
└─────────────────────────────────────────────────────────────┘
```

### File Storage Structure

```
S3 Bucket (or equivalent):
toolkits/
  aster/
    aster-1.0.0.tar.gz
    aster-1.1.0.tar.gz
  heptapod/
    heptapod-2.0.0.tar.gz
  metadata/
    logos/
      aster.png
      heptapod.png

Local Installation (~/.scitoolkit/):
  config.json              # User settings, API key
  toolkits/
    aster/
      venv/                # Isolated Python environment
      tools/               # Orchestral tool definitions
      toolkit.yaml         # Metadata
      requirements.txt
    heptapod/
      venv/
      tools/
      toolkit.yaml
      requirements.txt
```

## Phase 1: Foundation (Weeks 1-4)

### 1.1 CLI Tool Core (`scitoolkit` package)

**Goal**: Enable basic toolkit creation and structure

- [ ] Create CLI entry point using Click or Typer
- [ ] Implement `scitoolkit init` command
  - Generate toolkit.yaml template
  - Create directory structure (tools/, README.md, requirements.txt)
  - Interactive prompts for metadata
- [ ] Implement `scitoolkit validate` command
  - Check toolkit.yaml schema
  - Validate tool definitions (Orchestral format)
  - Check for required files
- [ ] Create toolkit.yaml schema definition
- [ ] Write tests for CLI commands

**Deliverable**: Scientists can run `scitoolkit init` to scaffold a toolkit

### 1.2 Backend API (stk-backend repo)

**Goal**: Accept and serve toolkit uploads

- [ ] Set up FastAPI project structure
- [ ] Configure PostgreSQL database
  - Users table (id, email, api_key_hash, created_at)
  - Toolkits table (id, name, category, version, author_id, description, downloads, created_at)
  - Versions table (id, toolkit_id, version, file_path, published_at)
- [ ] Implement authentication middleware (API key verification)
- [ ] Create API endpoints:
  - POST /api/auth/register (create account)
  - POST /api/auth/apikey (generate API key)
  - POST /api/upload (upload toolkit, auth required)
  - GET /api/download/:name/:version (download toolkit)
  - GET /api/registry (list all toolkits with metadata)
  - GET /api/toolkit/:name (get toolkit details)
- [ ] Set up S3/cloud storage integration
- [ ] Add file upload validation (size limits, format checks)
- [ ] Deploy to Railway or Fly.io
- [ ] Set up CI/CD pipeline

**Deliverable**: API server accepting authenticated uploads

### 1.3 Frontend Website (stk-website repo)

**Goal**: Basic browsing and account management

- [ ] Set up Next.js or React project
- [ ] Design landing page
  - Hero section explaining SciToolkit
  - Featured toolkits showcase
  - Quick start guide
- [ ] Implement toolkit browse/search page
  - Grid/list view of toolkits
  - Filter by category (astro, hep, quantum, etc.)
  - Search by keyword
- [ ] Create toolkit detail page
  - Display README (rendered markdown)
  - Show metadata (author, version, downloads, category)
  - Display logo/screenshots
  - Installation instructions
  - Dependencies list
- [ ] Build user account pages
  - Sign up / login
  - Profile page
  - API key generation and management
  - "My Toolkits" dashboard
- [ ] Deploy to Netlify
- [ ] Connect to backend API

**Deliverable**: Live website at scitoolkit.org

## Phase 2: Publishing Workflow (Weeks 5-8)

### 2.1 CLI Publishing

- [ ] Implement `scitoolkit login` command
  - Store API key securely in ~/.scitoolkit/config.json
  - Verify key with backend
- [ ] Implement `scitoolkit publish` command
  - Run validation checks
  - Package toolkit into tar.gz
  - Upload to backend API
  - Handle versioning (auto-increment or manual)
- [ ] Add progress bars for uploads
- [ ] Handle errors gracefully (network issues, auth failures, etc.)
- [ ] Write end-to-end publishing tests

### 2.2 Manual Review System

- [ ] Create admin panel on website
- [ ] Build toolkit review queue
  - Pending submissions list
  - Code viewer
  - Approve/reject actions
- [ ] Implement email notifications
  - Submission received
  - Approved/rejected status
- [ ] Document review guidelines
  - Security checks
  - Code quality standards
  - Orchestral tool format compliance

**Deliverable**: Complete publish workflow from CLI to website

## Phase 3: Installation & Usage (Weeks 9-12)

### 3.1 CLI Installation

- [ ] Implement `scitoolkit search <query>` command
  - Search registry by name/description/category
  - Display results in terminal (rich formatting)
- [ ] Implement `scitoolkit install <name>` command
  - Download toolkit from registry
  - Create isolated venv in ~/.scitoolkit/toolkits/<name>/
  - Install dependencies from requirements.txt
  - Extract toolkit files
  - Register toolkit locally
- [ ] Implement `scitoolkit list` command
  - Show installed toolkits
  - Display versions, paths
- [ ] Implement `scitoolkit uninstall <name>` command
- [ ] Implement `scitoolkit update <name>` command
  - Check for newer versions
  - Update toolkit

### 3.2 Integration with Orchestral AI

- [ ] Create toolkit loader module
  ```python
  from scitoolkit import load_toolkit
  aster = load_toolkit('aster')
  tools = aster.get_tools()
  ```
- [ ] Implement tool discovery from installed toolkits
- [ ] Add Orchestral tool format parser
- [ ] Test with existing ASTER and HEPTAPOD toolkits

### 3.3 MCP Server Integration

- [ ] Implement `scitoolkit serve` command
  - Start MCP server serving all installed toolkits
  - Support both HTTP and STDIO transports
  - Auto-generate MCP server config
- [ ] Create helper for Claude Code integration
  - `scitoolkit configure claude-code`
  - Auto-update Claude Code's MCP config file
- [ ] Document MCP usage for other agent frameworks

**Deliverable**: End-to-end workflow: install → use in scripts → use in MCP

## Phase 4: Polish & Scale (Weeks 13-16)

### 4.1 Developer Experience

- [ ] Create comprehensive documentation
  - Getting started guide
  - Toolkit creation tutorial
  - API reference
  - Best practices
- [ ] Build example toolkits
  - Simple "hello world" toolkit
  - More complex examples for each category
- [ ] Create toolkit template repository on GitHub
- [ ] Add CLI auto-update checker
- [ ] Improve error messages and help text

### 4.2 Community Features

- [ ] Add toolkit ratings/reviews on website
- [ ] Implement toolkit usage analytics
  - Download counts
  - Active users (opt-in)
- [ ] Create changelog/release notes system
- [ ] Add toolkit dependencies/recommendations
  - "Tools that work well together"
- [ ] Build discussion/comment system (or link to GitHub Discussions)

### 4.3 Advanced Features

- [ ] Support for private toolkits (teams/organizations)
- [ ] Toolkit collections/bundles
- [ ] Automated testing for submitted toolkits (CI/CD)
- [ ] Sandboxed execution environment (Docker/containers)
- [ ] Web-based toolkit editor (stretch goal)
- [ ] VSCode extension for toolkit management (future)

## Phase 5: Launch & Growth (Week 17+)

### 5.1 Seed Content

- [ ] Migrate ASTER to scitoolkit format
- [ ] Migrate HEPTAPOD to scitoolkit format
- [ ] Migrate quantum toolkit to scitoolkit format
- [ ] Migrate neutrino toolkit to scitoolkit format
- [ ] Publish all 4 toolkits to registry

### 5.2 Marketing & Outreach

- [ ] Write launch blog post
- [ ] Post on relevant communities:
  - arXiv (relevant categories)
  - Academic Twitter/Mastodon
  - Physics/astronomy forums
  - AI/ML communities (HuggingFace, etc.)
- [ ] Create demo videos
- [ ] Reach out to research groups directly
- [ ] Present at conferences (if applicable)

### 5.3 Monitoring & Iteration

- [ ] Set up monitoring (uptime, errors, performance)
- [ ] Collect user feedback
- [ ] Track key metrics:
  - Number of toolkits
  - Number of users
  - Download counts
  - Active usage
- [ ] Regular updates based on feedback
- [ ] Build roadmap for future features

## Success Metrics

**Short-term (3 months):**
- 5+ toolkits published (including seed content)
- 20+ registered users
- 100+ toolkit downloads
- Website live and functional
- Full publish/install workflow working

**Medium-term (6 months):**
- 15+ toolkits across multiple categories
- 100+ registered users
- 1000+ toolkit downloads
- Active community engagement (issues, discussions)
- VSCode extension beta

**Long-term (12 months):**
- 50+ toolkits
- 500+ registered users
- Established as go-to platform for scientific AI tools
- Self-sustaining community contributions
- Potential partnerships with research institutions

## Technical Decisions

### Why Not Just Use PyPI?

1. **Curation**: We need manual review for quality/security
2. **Metadata**: Scientific tools need domain-specific metadata (category, research area, etc.)
3. **Isolation**: Need to manage conflicting dependencies across toolkits
4. **Discovery**: Domain-specific search and categorization
5. **UX**: Scientists shouldn't need to learn Python packaging

### Technology Choices

**Frontend**: Next.js/React
- Modern, well-supported
- Great for static/hybrid sites
- Netlify deployment is free

**Backend**: Python FastAPI
- Fast, modern Python web framework
- Type hints & validation built-in
- Great docs, async support
- Native Python matches our audience

**Database**: SQLite or PostgreSQL
- SQLite: Simple, serverless, perfect for getting started
- PostgreSQL: Can upgrade later if needed
- Running on Triton home server

**Storage**: Local filesystem on Triton
- Simple directory structure for toolkit files
- Can add S3/cloud storage later if needed
- Direct file serving via FastAPI

**CLI**: Click or Typer
- Standard Python CLI frameworks
- Rich terminal output support
- Easy to test

## Risks & Mitigations

**Risk**: Low adoption
- **Mitigation**: Seed with quality toolkits, active marketing, make it genuinely easier than alternatives

**Risk**: Security vulnerabilities in submitted code
- **Mitigation**: Manual review initially, sandboxing later, clear security guidelines

**Risk**: Infrastructure costs grow too fast
- **Mitigation**: Using home server (Triton) keeps costs near zero initially, can migrate to cloud later if needed

**Risk**: Maintenance burden
- **Mitigation**: Build automated systems (CI/CD, testing), keep scope focused initially

**Risk**: Another platform does this better
- **Mitigation**: Focus on scientific domain expertise, tight Orchestral integration, community

## Open Questions

1. **Versioning strategy**: Semantic versioning enforced? Allow version deletion?
2. **Toolkit dependencies**: Can toolkits depend on other toolkits?
3. **Pricing model**: Forever free, or freemium model eventually?
4. **Governance**: How to handle disputes, moderation, etc.?
5. **Legal**: Terms of service, code licenses, liability?

## Next Immediate Steps

1. Set up GitHub repositories structure
2. Initialize backend API project
3. Initialize frontend project
4. Create detailed architecture diagrams
5. Start Phase 1 implementation
