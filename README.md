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

## User Roles

- Super Admin
- Admin
- Agent
- User

Role-based authorization is implemented to restrict access to sensitive operations.

---

## Project Management

- Create Project
- Update Project
- Delete Project
- Get Project
- List Projects
- Manage Project Members
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

Example Workflow:

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

Comments are stored separately from tickets.

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
| POST | /projects/{project_id}/members |
| GET | /projects/{project_id}/members |

---

## Status

| Method | Endpoint |
|---------|----------|
| POST | /status |
| GET | /status |
| PUT | /status/{id} |
| DELETE | /status/{id} |

---

## Tickets

| Method | Endpoint |
|---------|----------|
| POST | /tickets |
| GET | /tickets |
| GET | /tickets/{id} |
| PUT | /tickets/{id} |
| DELETE | /tickets/{id} |

---

## Comments

| Method | Endpoint |
|---------|----------|
| POST | /comments |
| GET | /comments/{ticket_id} |

---

## Notifications

| Method | Endpoint |
|---------|----------|
| GET | /notifications |
| PUT | /notifications/{id}/read |

---

# 🗄 Database Collections

```
users

projects

project_members

statuses

tickets

comments

notifications
```

Projects are stored in `projects`, memberships in `project_members`, and tickets
reference their project using `project_id` and `project_key`.

This is a breaking storage-schema change for databases created by earlier
versions. Migrate existing collections and reference fields before deployment.

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

---

# 🔑 Environment Variables

Create a `.env` file.

```env
MONGO_URI=mongodb://localhost:27017

DATABASE_NAME=ticket_management

SECRET_KEY=your_secret_key

ALGORITHM=HS256

ACCESS_TOKEN_EXPIRE_MINUTES=30
```

---

# ▶ Running the Project

```bash
uvicorn app.main:app --reload
```

API

```
http://127.0.0.1:8000
```

Swagger UI

```
http://127.0.0.1:8000/docs
```

ReDoc

```
http://127.0.0.1:8000/redoc
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
