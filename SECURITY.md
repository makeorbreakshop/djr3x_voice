# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| main    | :white_check_mark: |
| < 1.0   | :x:                |

## Reporting a Vulnerability

We take the security of DJ R3X Voice seriously. If you believe you have found a security vulnerability, please report it to us as described below.

### How to Report

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please report them by:

1. **Email:** Contact the maintainers directly (if email is provided in the repository)
2. **Private vulnerability reporting:** Use GitHub's private vulnerability reporting feature if enabled

### What to Include

Please include as much of the following information as possible:

- Type of issue (e.g., buffer overflow, SQL injection, cross-site scripting, etc.)
- Full paths of source file(s) related to the issue
- Location of the affected source code (tag/branch/commit or direct URL)
- Step-by-step instructions to reproduce the issue
- Proof-of-concept or exploit code (if possible)
- Impact of the issue, including how an attacker might exploit it

### Response Timeline

- **Initial Response:** Within 48 hours
- **Status Update:** Within 7 days
- **Resolution Timeline:** Depends on complexity, typically within 30-90 days

### What to Expect

1. **Acknowledgment:** We will acknowledge receipt of your report
2. **Assessment:** We will assess the vulnerability and its impact
3. **Resolution:** We will work on a fix
4. **Disclosure:** We will coordinate disclosure with you

## Security Best Practices for Users

### API Keys

- **Never commit API keys** to version control
- Use the provided `env.example` as a template
- Store keys in environment variables or `.env` file (gitignored)
- Rotate keys if you suspect they've been exposed

### Hardware Security

- The Arduino communication uses serial ports - ensure physical security
- LED patterns could theoretically be used for visual data exfiltration in sensitive environments

### Data Privacy

- Voice transcriptions are processed through third-party APIs (Deepgram)
- LLM responses go through OpenAI/Anthropic APIs
- The `memory_data/` directory contains personal interaction history - do not share
- Face recognition training data in `vision_data/training/` contains personal images

### Network Security

- The web dashboard runs on localhost by default
- Do not expose the dashboard to public networks without authentication
- API communications use HTTPS

## Known Security Considerations

### Third-Party Services

This project integrates with several third-party services:

| Service | Data Sent | Privacy Policy |
|---------|-----------|----------------|
| Deepgram | Audio transcriptions | [Link](https://deepgram.com/privacy) |
| OpenAI | Text prompts/responses | [Link](https://openai.com/privacy) |
| Anthropic | Text prompts/responses | [Link](https://www.anthropic.com/privacy) |
| ElevenLabs | Text for speech synthesis | [Link](https://elevenlabs.io/privacy) |

### Local Data Storage

The following data is stored locally and should be protected:

- `cantina_os/memory_data/` - User interaction history and profiles
- `cantina_os/vision_data/` - Face recognition training images
- `.env` - API keys and secrets
- `logs/` - May contain conversation content

## Responsible Disclosure

We kindly ask that you:

- Give us reasonable time to fix the issue before public disclosure
- Make a good faith effort to avoid privacy violations, data destruction, or service interruption
- Do not access or modify data belonging to others

We commit to:

- Responding promptly to your report
- Keeping you informed of our progress
- Crediting you (if desired) when we announce the fix

## Security Updates

Security updates will be released as:

- Patch releases for critical vulnerabilities
- Regular releases for less severe issues
- Security advisories on GitHub

Subscribe to repository notifications to stay informed of security updates.
