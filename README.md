# Swagger2DCAT – I14Y Dataservice Description Generator

This application helps you **easily generate a standardized description for your API (dataservice) on the Swiss interoperability platform [I14Y](https://input.i14y.admin.ch/)**, starting from your technical Swagger/OpenAPI documentation.

- **Input:** Just provide your Swagger/OpenAPI URL (or a link to your Swagger UI).
- **Output:** Get a ready-to-use JSON description for I14Y, including multilingual metadata, publisher info, and more.

You can run this tool **on-premise** or use it directly from the [I14Y Toolbox](https://i14y-ch.github.io/toolbox/).

---

## Features

- **Automatic extraction** of API metadata from Swagger/OpenAPI (including from Swagger UI HTML pages)
- **AI-powered description generation** (OpenAI, optional)
- **Multilingual support:** Translate your API description into German, French, Italian (DeepL, optional)
- **Easy review and editing** of all metadata before upload
- **Direct upload** to I14Y or download as JSON file
- **Publisher/agency lookup** from I14Y Admin API and Swiss Staatskalender

---

## Quick Start

### 1. Local Setup

```bash
git clone https://github.com/I14Y-ch/swagger2dcat.git
cd swagger2dcat2
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.template .env
# Edit .env and add your API keys (see below)
flask run
```

- Open [http://localhost:5000](http://localhost:5000) in your browser.

### 2. Docker

```bash
docker build -t swagger2dcat .
docker run -p 5000:5000 --env-file .env swagger2dcat
```

---

## Configuration

You need API keys for some features:

- `OPENAI_API_KEY` (optional, for AI-generated descriptions)
- `DEEPL_API_KEY` (optional, for translations)
- `SECRET_KEY` (required, for Flask session security)

Set these in your `.env` file or as environment variables.

---

## Usage

1. **Enter your Swagger/OpenAPI URL** (can be a direct JSON or a Swagger UI page)
2. *(Optional)* Add a landing page/documentation URL for richer descriptions
3. **Review and edit** the extracted metadata
4. *(Optional)* Use AI to generate a better description
5. *(Optional)* Translate to other languages
6. **Download the JSON** or **upload directly to I14Y**

---

## Project Structure

```
swagger2dcat/
├── app.py                  # Main Flask application
├── utils/                  # Helper modules
│   ├── swagger_utils.py    # Swagger/OpenAPI parsing
│   ├── openai_utils.py     # OpenAI integration
│   ├── deepl_utils.py      # DeepL translation
│   └── i14y_utils.py       # I14Y API helpers
├── templates/              # HTML templates
├── static/                 # CSS/JS
└── requirements.txt        # Python dependencies
```

---

**Questions or feedback?**  
See the [I14Y Toolbox](https://i14y-ch.github.io/toolbox/) or contact the [interoperability service](mailto:i14y@bfs.admin.ch).