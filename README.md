# 🎫 Ticket Management System API

A production-style Ticket Management System built with **FastAPI** and **MongoDB**. The application provides authentication, role-based authorization, project management, ticket management, comments, notifications, and customizable workflows.

---

# 🚀 Features

## Authentication

- User Registration
- User Login
- JWT Authentication
- Password Hashing (bcrypt)
- Protected Routes

---

## Authorization Roles

There are only two global roles:

- `super_admin` manages system-wide configuration and administrators.
- `admin` manages user activation and system-wide reports.

Ordinary accounts have no global role. Their access is determined by their
per-project `lead`, `member`, or `reporter` membership and that project's
permission scheme. Ticket assignees are not a separate global “agent” role.

---

## Project Management

- Create Project
- Update Project
- Delete Project
- Get Project
- List Projects
- Manage Project Members
- Configure Per-Project Role Permissions
- Configure Project Workflows

Each project contains:

- Name
- A unique 2–10 character uppercase key
- An independent ticket number sequence
- Assigned Status Workflow

Example:

```
Office Management System
Code: OMS

Generated Tickets:

OMS-1
OMS-2
OMS-3
```

---

## Status Management

Global statuses can be created and assigned to projects.

Projects define named transition graphs over their assigned statuses. A transition
can accept multiple source statuses and can restrict execution by project role or
require an assignee.

Example workflow:

```
Open
↓

In Progress
↓

Resolved
↓

Closed
```

Each project can have its own workflow. A super admin or the project's lead can
configure the project, its members, and its assigned statuses.

---

## Project Permission Schemes

Every project starts with the standard `lead`, `member`, and `reporter`
capabilities. A project lead or super admin can then grant or revoke capabilities
for one role in that project without changing the same role in other projects.

For example, this allows reporters to upload attachments in one project only:

```http
PUT /projects/{project_id}/permissions
Content-Type: application/json

{
  "role": "reporter",
  "grants": ["upload_attachments"],
  "revocations": []
}
```

`GET /projects/{project_id}/permissions` returns each role's defaults, overrides,
and effective capabilities. `manage_project` cannot be overridden, preventing a
scheme from granting project administration to another role or locking project
leads out of configuration.

The legacy `Permission` enum and runtime `Capability` enum are intentionally
separate. `Permission` describes configurable role grants exposed by the
project-permissions API, while `Capability` is the endpoint authorization
vocabulary and also covers contextual actions such as ticket transitions and
attachment exceptions.

---

## Ticket Management

Create Ticket

Update Ticket

Delete Ticket

Get Ticket

List Tickets

Each ticket contains:

- Ticket ID
- Ticket Key
- Title
- Description
- Category
- Priority
- Current Status
- Project
- Assigned Agent
- Created By
- Due Date
- Created Date
- Updated Date

Example Ticket:

```
OMS-15
```

---

## Ticket Comments

Users can:

- Add Comment
- View Comments
- Reply in threads
- Edit or delete their own comments
- Mention project participants with `@username`

Comments are stored separately from tickets. Reporter-visible comments remain
public-only; internal comments and their mention notifications are restricted to
participants who can view them.

---

## Notifications

Notifications are generated for important ticket activities.

Examples:

- Ticket Assigned
- Ticket Status Updated
- Ticket Comment Added

---

# 🛠 Tech Stack

Backend

- FastAPI

Database

- MongoDB

Authentication

- JWT

Validation

- Pydantic

Password Hashing

- Passlib (bcrypt)

ODM

- PyMongo

## Docker

Docker Compose starts the API and MongoDB together:

```bash
docker compose up --build
```

The API is available at `http://localhost:7000`, with OpenAPI documentation at
`http://localhost:7000/docs`. Set `SECRET_KEY` in the environment before
starting Compose for anything beyond local development. MongoDB data and local
attachments use named Docker volumes and survive container restarts.

---

# 📂 Project Structure

```
app/
│
├── api/
│   ├── auth.py
│   ├── users.py
│   ├── project.py
│   ├── tickets.py
│   ├── status.py
│   ├── comments.py
│   └── notifications.py
│
├── models/
│
├── schemas/
│
├── database/
│
├── utils/
│
├── dependencies/
│
├── config.py
│
└── main.py
```

---

# 🔐 Authentication

The API uses **JWT Bearer Tokens**.

Login

```
POST /login
```

Response

```json
{
    "access_token": "...",
    "token_type": "bearer"
}
```

Include the token in every protected request.

```
Authorization: Bearer <your_token>
```

---

# 📌 Main API Endpoints

## Authentication

| Method | Endpoint |
|---------|----------|
| POST | /register |
| POST | /login |

---

## Users

| Method | Endpoint |
|---------|----------|
| GET | /users |
| GET | /users/{id} |
| PUT | /users/{id} |
| DELETE | /users/{id} |

---

## Projects

| Method | Endpoint |
|---------|----------|
| POST | /projects |
| GET | /projects |
| GET | /projects/{project_id} |
| PUT | /projects/{project_id} |
| DELETE | /projects/{project_id} |
| PATCH | /projects/{project_id}/statuses |
| GET | /projects/{project_id}/workflow |
| PUT | /projects/{project_id}/workflow |
| POST | /projects/{project_id}/members |
| GET | /projects/{project_id}/members |

---

## Status

| Method | Endpoint |
|---------|----------|
| POST | /statuses/ |
| GET | /statuses/ |
| PATCH | /statuses/{id} |
| DELETE | /statuses/{id} |

---

## Tickets

| Method | Endpoint |
|---------|----------|
| POST | /tickets |
| GET | /tickets |
| GET | /tickets/search |
| GET | /tickets/{project_key} |
| GET | /tickets/{id} |
| PUT | /tickets/{id} |
| DELETE | /tickets/{id} |
| GET | /tickets/{ticket_id}/transitions |
| POST | /{ticket_id}/transitions/{transition_id} |

Board and global ticket search support status, priority, category, issue type,
labels, epic, assignee, overdue, and sorting filters. Text search matches ticket
number, title, and description. Global search returns only projects the current
user can access; a super-admin can search every project.

---

## Agile delivery (sprints)

| Method | Endpoint |
|---------|----------|
| POST | /projects/{project_id}/sprints |
| GET | /projects/{project_id}/sprints |
| GET | /sprints/{sprint_id} |
| PATCH | /sprints/{sprint_id} |
| DELETE | /sprints/{sprint_id} |
| POST | /sprints/{sprint_id}/start |
| POST | /sprints/{sprint_id}/complete |
| GET | /projects/{project_id}/backlog |
| GET | /projects/{project_id}/board |
| PATCH | /tickets/{ticket_id}/sprint |
| PATCH | /tickets/{ticket_id}/story-points |
| PATCH | /tickets/{ticket_id}/backlog-position |
| PATCH | /tickets/{ticket_id}/board-position |

Sprints follow `planned → active → completed`, with at most one active sprint
per project. Project leads manage the lifecycle; leads and members can estimate,
assign, and reorder tickets. A ticket with no `sprint_id` belongs to the ordered
project backlog. `board_position` orders a ticket within its current status
column without changing the project's workflow/status model.

Starting a sprint snapshots its original commitment. Completing it records
completed story points as immutable velocity history and moves incomplete work
back to the backlog. Completed tickets remain linked to the completed sprint.

---

## Saved filters

| Method | Endpoint |
|---------|----------|
| GET | /filters |
| POST | /filters |
| DELETE | /filters/{filter_id} |

Saved filters belong to the authenticated user. Example request:

```json
{
  "name": "Urgent backend work",
  "query_params": {
    "scope": "global",
    "priority": "urgent",
    "labels": ["backend"]
  }
}
```

---

## Reporting and dashboards

| Method | Endpoint |
|---------|----------|
| GET | /reports/my-dashboard |
| GET | /reports/projects/{project_id}/summary |
| GET | /reports/projects/{project_id}/velocity |
| GET | /reports/agent-tickets |
| GET | /reports/overdue-tickets |
| GET | /reports/avg-resolution-time |
| GET | /tickets_summary?project_id={project_id} |

The personal dashboard returns the current user's active created and assigned
issues only from projects they can access. Project summaries use dynamic status
and priority buckets, so custom workflows are supported. Administrative aggregate
reports remain restricted to admins and super-admins.

Project summaries include backlog totals and the active sprint's progress.
Velocity reports return the completed story-point series, average/latest
velocity, and trend across the requested recent completed sprints.

---

## Comments

| Method | Endpoint |
|---------|----------|
| POST | /tickets/{ticket_id}/comments |
| GET | /tickets/{ticket_id}/comments |
| POST | /comments/{comment_id}/replies |
| PATCH | /comments/{comment_id} |
| DELETE | /comments/{comment_id} |

Pass `threaded=true` to the ticket comment listing endpoint to nest replies under
their parent comments.

---

## Attachments

| Method | Endpoint |
|---------|----------|
| POST | /tickets/{ticket_id}/attachments |
| GET | /tickets/{ticket_id}/attachments |
| GET | /attachments/{attachment_id} |
| DELETE | /attachments/{attachment_id} |

All attachment operations require access to the owning ticket. Only the uploader,
a project lead, or a super admin can delete an attachment.

---

## Notifications

| Method | Endpoint |
|---------|----------|
| GET | /notifications |
| GET | /notifications/{notification_id} |
| PUT | /notifications/{notification_id}/read |
| PUT | /notifications/mark-all-read |
| GET | /notifications/preferences/{project_id} |
| PUT | /notifications/preferences/{project_id} |

Notification delivery is enabled by default. A project member can disable all
notifications for one project or opt out of specific event types:

```json
{
  "enabled": true,
  "disabled_event_types": ["ticket_transitioned", "comment_created"]
}
```

---

# 🗄 Database Collections

```
users

projects

project_members

statuses

transitions

sprints

tickets

comments

attachments

notifications

notification_preferences

saved_filters
```

Projects are stored in `projects`, memberships in `project_members`, and tickets
reference their project using `project_id` and `project_key`. Named workflow
transitions are stored separately in `transitions` and reference their project.
Sprint lifecycle and velocity snapshots are stored in `sprints`; tickets store
their optional `sprint_id`, `story_points`, backlog order, and board-column order.

This is a breaking storage-schema change for databases created by earlier
versions. Migrate existing collections and reference fields before deployment.

## Attachment storage

Development uses local storage by default:

```env
ATTACHMENT_STORAGE_BACKEND=local
ATTACHMENT_LOCAL_DIR=uploads/tickets
```

Production can use AWS S3 or another S3-compatible service through the same API:

```env
ATTACHMENT_STORAGE_BACKEND=s3
ATTACHMENT_S3_BUCKET=ticket-attachments
ATTACHMENT_S3_PREFIX=production
ATTACHMENT_S3_REGION=us-east-1
ATTACHMENT_S3_ENDPOINT_URL=https://s3.example.com
ATTACHMENT_S3_ADDRESSING_STYLE=path
ATTACHMENT_S3_SSE=AES256
```

`ATTACHMENT_S3_ENDPOINT_URL`, `ATTACHMENT_S3_PREFIX`, and `ATTACHMENT_S3_SSE` are
optional. Boto3 uses its normal credential provider chain, so production should
prefer workload or instance credentials; `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY` can be used for local S3-compatible services.

Attachment metadata records its storage backend and object key. Migrate existing
local objects and metadata before changing a deployed environment from `local` to
`s3`.

---

# ⚙ Installation

Clone the repository

```bash
git clone <repository-url>
```

Move into the project

```bash
cd ticket-management-system
```

Create a virtual environment

```bash
python -m venv venv
```

Activate the environment

Linux / macOS

```bash
source venv/bin/activate
```

Windows

```bash
venv\Scripts\activate
```

Install dependencies

```bash
pip install -r requirements.txt
```

Install the Git hooks once for this clone:

```bash
pre-commit install
```

---

# 🔑 Environment Variables

Create a `.env` file.

Configuration is validated with `pydantic-settings` at startup. Copy
`.env.example` and replace its placeholder values:

```bash
cp .env.example .env
```

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=mydatabase
SECRET_KEY=replace-with-a-random-value-of-at-least-32-characters
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
PASSWORD_RESET_EXPIRE_MINUTES=15
```

`SECRET_KEY` and `MONGO_URI` are required. Generate a suitable local JWT key
with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

If `git ls-files .env` prints `.env`, the file was committed before the ignore
rule was added. Remove it from Git tracking before adding real secrets (the
local file is kept):

```bash
git rm --cached .env
```

Rotate any secret that was already committed, because adding an ignore rule
does not remove it from Git history.

Public registration, login, and password-reset endpoints use Mongo-backed
fixed-window rate limits. Their defaults are in `.env.example`; counters are
automatically removed through a MongoDB TTL index. Leave
`TRUST_PROXY_HEADERS=false` unless the application is behind a trusted reverse
proxy that replaces incoming forwarding headers.

---

# ▶ Running the Project

```bash
uvicorn app.main:app --reload
```

API

```
http://127.0.0.1:7000
```

Swagger UI

```
http://127.0.0.1:7000/docs
```

ReDoc

```
http://127.0.0.1:7000/redoc
```

---

# 📖 Example Workflow

```
Super Admin

↓

Creates Project

↓

Creates Workflow Statuses

↓

Registers Users

↓

Creates Ticket

↓

Assigns Ticket

↓

Agent Works on Ticket

↓

Comments Added

↓

Status Updated

↓

Resolved

↓

Closed
```

---

# 🔒 Security

- JWT Authentication
- Password Hashing
- Role-Based Access Control
- Request Validation
- Secure Password Storage

---


# 👨‍💻 Author

**Ibrahim**

Backend Developer (FastAPI • MongoDB)

---

# 📄 License

This project is created for educational and portfolio purposes.
