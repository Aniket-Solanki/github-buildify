# Buildify

Buildify is an AI-powered tool that automatically generates professional resumes directly from any public or private GitHub repository.

By analyzing the structure, code, and commit history of a repository, Buildify creates a tailored, nicely formatted resume that highlights your technology stack, key contributions, and project overview.

## Features
- **AI-Powered Generation**: Extracts meaningful insights and contributions using advanced AI models.
- **Support for Large Repositories**: Analyzes up to 50,000 files and 5GB repositories locally.
- **Export to PDF**: Generate and download a beautifully styled PDF resume instantly.
- **Copy as LaTeX**: Easily extract the resume in LaTeX format for further customization.
- **Fast & Responsive**: Real-time WebSocket streaming of generation progress.

## Setup Instructions

1. Clone the repository:
   ```bash
   git clone https://github.com/Aniket-Solanki/github-buildify.git
   cd github-buildify
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   pip install -r requirements.txt
   ```

3. Configure Environment Variables:
   Copy `.env.example` to `.env` and fill in your keys:
   ```
   # Add your Gemini API key (or other supported providers)
   GEMINI_API_KEY=your_key_here
   
   # Add your GitHub OAuth credentials
   GITHUB_CLIENT_ID=your_client_id
   GITHUB_CLIENT_SECRET=your_client_secret
   
   # Add your Redis credentials
   REDIS_HOST=your_redis_host
   REDIS_PORT=your_redis_port
   REDIS_PASSWORD=your_redis_password
   ```

4. Run the application:
   ```bash
   uvicorn app:app --host 0.0.0.0 --port 8080 --reload
   ```

## License
MIT License
