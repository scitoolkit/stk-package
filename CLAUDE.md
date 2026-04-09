# SciToolkit - Project Context for Claude

This file provides context for AI assistants (like Claude) working on the SciToolkit project.

## What is SciToolkit?

SciToolkit is a community-driven platform for scientific agentic tools. It's like an app store or VSCode extensions marketplace, but specifically for AI tools used in scientific research. The goal is to make it as easy as possible for scientists to:

- **Create** tools for AI agents using familiar Python
- **Publish** tools without needing to learn Python packaging
- **Share** tools with the research community
- **Search** and discover tools across scientific domains
- **Download** and use tools seamlessly in their agent workflows
- **Integrate** tools with agent frameworks (Claude Code, Codex, etc.)

## The Problem We're Solving

Scientists are building AI tools for their domains (astrophysics, high-energy physics, quantum computing, neutrino physics, etc.) but there's no centralized, easy way to share them. Current options are inadequate:

1. **GitHub repos**: Hard to discover, no standardization, dependency conflicts
2. **PyPI**: Too technical for non-package-experts, no curation, not domain-specific
3. **Copy-paste code**: Not maintainable, no versioning, security risks

## Key Design Principles

1. **Scientist-Friendly**: Target audience is physicists/researchers comfortable with Python but NOT packaging experts
2. **Simple Publishing**: Should be as easy as `scitoolkit publish` (like `git push`)
3. **Curated Quality**: Manual review initially to ensure security and quality
4. **Isolated Execution**: Each toolkit runs in its own environment to avoid dependency conflicts
5. **MCP Compatible**: Full integration with Model Context Protocol for broad agent compatibility
6. **Orchestral Native**: Built around Orchestral AI's universal tool representation

## Project Information

- **Author**: Alex Roman
- **Package Name**: `scitoolkit` (secured on PyPI)
- **Domain**: scitoolkit.org (purchased)
- **GitHub Org**: https://github.com/scitoolkit
- **Contact**: scitoolkit.dev@gmail.com

### Repository Structure

```
scitoolkit (GitHub org)
├── scitoolkit           # Python package/CLI (this repo)
├── stk-website          # Frontend (Next.js/React on Netlify)
└── stk-backend          # API server (FastAPI on Railway/Fly.io)
```

## Architecture

### Three-Component System

**1. Website (scitoolkit.org)**
- Browse and search toolkits
- User accounts and API key generation
- Toolkit detail pages (README, metadata, download stats)
- Admin panel for toolkit review

**2. Backend API**
- Authentication (API key management)
- Toolkit upload/download endpoints
- Registry index (JSON API)
- PostgreSQL database (users, toolkits, downloads)
- S3/cloud storage for toolkit files

**3. CLI Tool (`pip install scitoolkit`)**
- `scitoolkit init` - Create toolkit from template
- `scitoolkit login` - Authenticate with API key
- `scitoolkit publish` - Upload toolkit to registry
- `scitoolkit search` - Find toolkits
- `scitoolkit install` - Download and setup toolkit in isolated venv
- `scitoolkit serve` - Start MCP server for installed toolkits

## Toolkit Structure

A toolkit is a directory with standardized structure:

```
my-toolkit/
├── toolkit.yaml          # Metadata (name, description, author, version, category)
├── tools/                # Orchestral tool definitions (Python files)
│   ├── tool1.py
│   └── tool2.py
├── requirements.txt      # Python dependencies
├── README.md            # Documentation (rendered on website)
├── logo.png             # Optional logo
└── screenshots/         # Optional screenshots
```

### toolkit.yaml Example

```yaml
name: aster
category: astro
version: 1.0.0
description: "Agentic Science Toolkit for Exoplanet Research"
author: "Alex Roman"
license: "MIT"
homepage: "https://github.com/..."
keywords:
  - exoplanets
  - astrophysics
  - forward-modeling
```

## User Workflows

### For Toolkit Creators (Scientists):

1. Register account on scitoolkit.org → get API key
2. Run `scitoolkit init` to create toolkit template
3. Add their tools (Orchestral format)
4. Fill in toolkit.yaml metadata
5. Run `scitoolkit publish` to upload
6. Toolkit goes into review queue
7. Once approved, appears on scitoolkit.org

### For Toolkit Users:

**Option 1: Scripting**
```python
from scitoolkit import load_toolkit

aster = load_toolkit('aster')
tools = aster.get_tools()

# Use with Orchestral or other framework
agent = Agent(tools=tools)
```

**Option 2: MCP Server**
```bash
scitoolkit install aster heptapod
scitoolkit serve  # Starts MCP server
# Tools now available to Claude Code, Codex, etc.
```

**Option 3: Auto-configure agents**
```bash
scitoolkit configure claude-code
# Automatically updates Claude Code config to use SciToolkit MCP server
```

## Technology Stack

### Current (Python Package):
- Python 3.8+
- PyPI for distribution
- Setuptools for packaging

### Planned:

**Frontend:**
- Next.js or React
- Deployed on Netlify
- Tailwind CSS for styling

**Backend:**
- Python FastAPI
- PostgreSQL database
- S3/Cloudflare R2 for file storage
- Deployed on Railway or Fly.io

**CLI:**
- Click or Typer for CLI framework
- Rich for terminal formatting
- Requests for API calls
- Virtualenv management for isolated toolkits

## Existing Toolkits (Seed Content)

These will be the initial toolkits published to demonstrate the platform:

1. **ASTER** - Agentic Science Toolkit for Exoplanet Research
2. **HEPTAPOD** - High Energy Physics Toolkit for Agentic ... (name TBD)
3. **Quantum Toolkit** - Quantum computing research tools
4. **Neutrino Toolkit** - Neutrino physics tools

## Integration with Orchestral AI

Orchestral AI (created by Alex) is a framework for building AI agents with tools. Key features:

- Universal tool representation
- MCP format conversion built-in
- Support for both HTTP and STDIO MCP servers
- Native tool execution without MCP overhead

SciToolkit tools are defined in Orchestral format, which means:
- Tools can run natively in Orchestral
- Tools can be served as MCP servers
- Tools work with Claude Code, Codex, LangChain, etc.

## Current Status

**Completed:**
- Package name secured on PyPI (v0.1.0 placeholder)
- Domain purchased (scitoolkit.org)
- GitHub organization created
- Frontend repo created (stk-website)
- Netlify deployment configured
- Gmail account created
- Initial project planning

**Next Steps:**
- Set up backend API repository
- Implement CLI commands (init, publish)
- Build frontend website
- Create backend API endpoints
- Test full publish/install workflow

## File Locations

**This Repository (`scitoolkit`):**
- `/scitoolkit/` - Python package source code
  - `__init__.py` - Package initialization
  - `astro.py` - Placeholder for astro category
  - `hep.py` - Placeholder for HEP category
  - `quantum.py` - Placeholder for quantum category
  - `neutrino.py` - Placeholder for neutrino category
- `pyproject.toml` - Package configuration
- `README.md` - Package documentation
- `LICENSE` - MIT License
- `.gitignore` - Git ignore rules
- `PLAN.md` - Detailed implementation plan
- `CLAUDE.md` - This file

**Local User Installation:**
```
~/.scitoolkit/
├── config.json              # API key, user settings
└── toolkits/
    ├── aster/
    │   ├── venv/            # Isolated Python environment
    │   ├── tools/           # Tool definitions
    │   ├── toolkit.yaml
    │   └── requirements.txt
    └── heptapod/
        └── ...
```

**Cloud Storage:**
```
S3 Bucket:
└── toolkits/
    ├── aster/
    │   ├── aster-1.0.0.tar.gz
    │   └── aster-1.1.0.tar.gz
    ├── heptapod/
    │   └── heptapod-2.0.0.tar.gz
    └── metadata/
        └── logos/
            ├── aster.png
            └── heptapod.png
```

## Important Notes for AI Assistants

### When Working on This Project:

1. **Target Audience**: Always remember the user is a scientist, not a DevOps engineer. Make things simple.

2. **Don't Flood PyPI**: We're NOT creating individual PyPI packages for each toolkit. The registry is separate from PyPI.

3. **Orchestral Format**: Tools are defined in Orchestral AI's format. This is the standardized representation we use.

4. **Isolated Environments**: Each toolkit MUST run in its own virtualenv to avoid dependency conflicts. This is critical.

5. **Manual Review**: Initially, all toolkit submissions go through manual review before appearing in the registry. This is for security and quality.

6. **MCP Compatibility**: Everything should work with MCP, but also work standalone with Orchestral.

7. **Categories**: Toolkits are organized by scientific domain (astro, hep, quantum, neutrino, etc.) - similar to arXiv categories.

### Common Patterns:

**CLI Command Structure:**
```python
# Use Click or Typer
import click

@click.group()
def cli():
    """SciToolkit - Scientific agentic tools made easy"""
    pass

@cli.command()
def init():
    """Initialize a new toolkit"""
    # Implementation
    pass
```

**API Client Pattern:**
```python
# CLI should use requests to talk to backend
import requests

API_BASE = "https://api.scitoolkit.org"

def upload_toolkit(api_key, toolkit_path):
    headers = {"Authorization": f"Bearer {api_key}"}
    with open(toolkit_path, 'rb') as f:
        response = requests.post(
            f"{API_BASE}/api/upload",
            headers=headers,
            files={'toolkit': f}
        )
    return response.json()
```

**Toolkit Loading Pattern:**
```python
# Load installed toolkit
def load_toolkit(name):
    toolkit_path = Path.home() / ".scitoolkit" / "toolkits" / name
    if not toolkit_path.exists():
        raise ValueError(f"Toolkit '{name}' not installed")

    # Activate venv and import tools
    # Return toolkit object with tools
    pass
```

### Security Considerations:

- API keys should be stored securely (not in plaintext if possible)
- Validate all toolkit uploads (size limits, file types)
- Sanitize user input before rendering on website
- Run untrusted code in isolated environments
- Review toolkit code before approval

### Testing Strategy:

- Unit tests for CLI commands
- Integration tests for API endpoints
- End-to-end tests for full workflows (init → publish → install → use)
- Mock API responses for offline testing
- Test with actual Orchestral AI integration

## Comparison to Similar Projects

**vs. PyPI:**
- More curated, domain-specific
- Easier for non-experts
- Built-in isolation and MCP support

**vs. Hugging Face:**
- Focused on tools, not models
- Scientific domain expertise
- Agent framework integration

**vs. GitHub:**
- Standardized structure
- Discovery and search
- Automated installation and environment setup

**vs. Docker Hub:**
- Lighter weight (no containers required initially)
- Python-native workflow
- Better for quick iteration

## Success Criteria

**MVP (Minimum Viable Product):**
- CLI can init, publish, install toolkits
- Website can browse and display toolkits
- Backend accepts uploads and serves downloads
- At least 4 toolkits published (seed content)
- End-to-end workflow works

**Version 1.0:**
- Manual review system working
- 10+ toolkits published
- 50+ users registered
- MCP integration working
- Documentation complete

**Long-term Vision:**
- Standard platform for scientific AI tools
- Hundreds of toolkits across domains
- Active community contributions
- Integration with major agent frameworks
- Potential VSCode extension
- Self-sustaining ecosystem

## Questions to Consider

When implementing features, keep these questions in mind:

1. Is this simple enough for a physicist with basic Python knowledge?
2. Does this maintain security and quality standards?
3. How does this scale if we have 100+ toolkits?
4. Is this compatible with the Orchestral AI workflow?
5. Does this work with MCP out of the box?
6. Can this be automated or does it need manual review?

## Getting Help

If you need clarification on:
- **Orchestral AI format**: Ask Alex or check Orchestral docs
- **Scientific domain questions**: Defer to domain experts
- **Architecture decisions**: Refer to PLAN.md or ask Alex
- **Technical implementation**: Standard Python/web best practices apply

## Common Commands Reference

```bash
# For developers working on scitoolkit package:
pip install -e .              # Install in editable mode
pytest                        # Run tests
python -m build              # Build package
twine upload dist/*          # Publish to PyPI

# For toolkit creators (end users):
pip install scitoolkit       # Install CLI
scitoolkit init              # Create new toolkit
scitoolkit login             # Authenticate
scitoolkit publish           # Upload toolkit
scitoolkit install aster     # Install toolkit
scitoolkit list              # Show installed toolkits
scitoolkit serve             # Start MCP server

# For toolkit users:
from scitoolkit import load_toolkit
aster = load_toolkit('aster')
```

## Resources

- **PLAN.md**: Detailed implementation roadmap
- **README.md**: Package documentation and usage
- **GitHub Issues**: Track bugs and features
- **scitoolkit.org**: Website (when live)
- **Orchestral AI docs**: Tool format reference

---

**Last Updated**: 2026-04-03
**Version**: 0.1.0 (initial planning phase)
