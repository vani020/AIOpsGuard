# AIOpsGuard
### AI-Driven Infrastructure Monitoring, Anomaly Detection, Root Cause Analysis & Intelligent Incident Response Platform

AIOpsGuard is an AI-driven infrastructure monitoring and incident response platform that combines OS monitoring, DBMS, Machine Learning, and LLMs. It detects system anomalies, predicts potential failures, analyzes logs and metrics to identify root causes, and provides intelligent recommendations, with human approval for critical actions.
Modern applications depend on servers, operating systems, databases, and services working continuously. High CPU usage, memory exhaustion, abnormal processes, application failures, and unusual logs can lead to service degradation or downtime.

Traditional monitoring systems generally generate alerts based on predefined thresholds but often do not explain **why the problem occurred, whether it may become critical, or what action should be taken**.

AIOpsGuard addresses this problem by integrating **Operating Systems, DBMS, Machine Learning, and Large Language Models (LLMs)** into a single platform.

---

## 🚀 Features

* 📊 Real-time CPU and memory monitoring
* ⚙️ Process and resource monitoring
* 📝 System and application log monitoring
* 🔍 ML-based anomaly detection
* 🔮 Potential failure-risk prediction
* 🧠 LLM-based root cause analysis
* 💡 Intelligent recommendations and preventive measures
* 🗄️ Historical incident storage and retrieval
* 👨‍💻 Human approval for critical actions
* 📈 Centralized infrastructure monitoring dashboard

---

## 🎯 Problem Statement

Modern infrastructure generates large amounts of metrics, processes, and logs. Existing monitoring solutions often work using predefined rules and process metrics and logs independently.

This makes it difficult to:

* Identify the actual root cause of an incident
* Predict failures before they become critical
* Understand unusual system behavior
* Reuse historical incident information
* Decide what corrective action should be taken
* Reduce troubleshooting time and system downtime

AIOpsGuard aims to transform infrastructure management from **reactive monitoring to proactive incident prevention**.

---

## 💡 Solution

AIOpsGuard combines infrastructure monitoring, database management, Machine Learning, and LLM-based analysis into a single platform.

```text
Infrastructure / Server
        ↓
Monitoring Agent
        ↓
CPU • Memory • Processes • Logs
        ↓
PostgreSQL Database
        ↓
Machine Learning
        ↓
Anomaly Detection & Failure Prediction
        ↓
LLM-based Root Cause Analysis
        ↓
Recommendations
        ↓
Human Approval
        ↓
Controlled Remediation
```

---

## 🏗️ System Architecture

### 1. Infrastructure Monitoring

The monitoring layer continuously collects:

* CPU utilization
* Memory utilization
* Running processes
* System logs
* Application logs

### 2. Database Layer

PostgreSQL is used to manage infrastructure and incident-related information.

The database covers:

* Servers
* Metrics
* Processes
* Logs
* Incidents
* Predictions
* Historical data

### 3. Machine Learning Layer

The ML module is responsible for:

* Detecting abnormal system behavior
* Identifying anomalies
* Predicting possible infrastructure failures
* Estimating potential failure risk

### 4. LLM Layer

The LLM analyzes current metrics, logs, and historical incident information to:

* Identify possible root causes
* Explain incidents
* Generate recommendations
* Suggest preventive measures

### 5. Incident Management

Detected incidents are stored in the database so that historical incidents can be retrieved and used as context for future analysis.

### 6. Human-in-the-Loop

Critical actions require administrator approval before execution, ensuring that important system changes remain under human supervision.

---

## 🛠️ Technology Stack

| Component        | Technology           |
| ---------------- | -------------------- |
| Frontend         | React.js             |
| Backend          | FastAPI / Python     |
| Database         | PostgreSQL           |
| Machine Learning | Python               |
| LLM              | Large Language Model |
| Operating System | Window               |
| API              | REST API             |
| Version Control  | Git & GitHub         |

---

## 💻 OS Concepts Used

The project incorporates several Operating System concepts:

* Process Management
* Thread Monitoring
* CPU Scheduling
* Resource Allocation
* Memory Management
* Synchronization
* Deadlock Handling

---

## 🗄️ DBMS Concepts Used

The database component applies:

* ER Modeling
* Normalization
* Primary and Foreign Keys
* Constraints
* SQL Joins
* Indexes
* Views
* Triggers
* Transactions

---

## 🤖 Machine Learning

Machine Learning is used to analyze infrastructure metrics and identify abnormal behavior.

### Anomaly Detection

The system analyzes metrics such as CPU and memory utilization to identify behavior that differs from normal system patterns.

### Failure Prediction

Historical infrastructure metrics and incident records can be used to identify patterns associated with potential future failures.

---

## 🧠 LLM-Based Root Cause Analysis

Once an anomaly is detected, relevant system metrics, logs, and historical information can be provided to the LLM.

For example:

```text
CPU Usage     : 96%
Memory Usage  : 89%
Process Count : High
Logs          : Multiple timeout errors
```

The LLM can analyze the available context and provide:

```text
Possible Root Cause:
High resource utilization may be affecting application
performance and causing timeout-related errors.

Recommended Action:
Investigate resource-intensive processes and review
related application/database activity.

Preventive Measure:
Monitor recurring resource spikes and application errors.
```

---

## 🔄 Incident Response Workflow

```text
Monitor Infrastructure
        ↓
Collect Metrics & Logs
        ↓
Store Data
        ↓
Detect Anomaly
        ↓
Predict Failure Risk
        ↓
Retrieve Historical Incidents
        ↓
Perform Root Cause Analysis
        ↓
Generate Recommendations
        ↓
Human Approval
        ↓
Controlled Action
        ↓
Store Incident Outcome
```

---

## 📊 Dashboard

The monitoring dashboard provides a centralized view of infrastructure health.

It can display:

* System health
* CPU usage
* Memory usage
* Running processes
* Recent logs
* Active incidents
* Anomaly alerts
* Failure predictions
* Root cause analysis
* Recommended actions
* Historical incidents
* Approval requests

---

## 🧪 Testing

The prototype is tested in a controlled Linux environment using simulated infrastructure scenarios such as:

* Resource overload
* High CPU utilization
* High memory utilization
* Abnormal processes
* Log anomalies
* Server failure scenarios
* Recovery scenarios

---

## 📁 Project Structure

```text
AIOpsGuard/
│
├── frontend/
│   ├── src/
│   ├── components/
│   ├── pages/
│   └── services/
│
├── backend/
│   ├── main.py
│   ├── api/
│   ├── models/
│   ├── services/
│   └── database/
│
├── ml/
│   ├── anomaly_detection/
│   ├── failure_prediction/
│   └── models/
│
├── llm/
│   ├── root_cause_analysis/
│   └── recommendations/
│
├── monitoring/
│   ├── system_metrics/
│   ├── process_monitor/
│   └── log_monitor/
│
├── database/
│   ├── schema.sql
│   ├── triggers.sql
│   └── seed.sql
│
├── tests/
│
├── requirements.txt
├── README.md
└── .env.example
```

---

## ⚙️ Installation & Setup

### Prerequisites

* Python 3.x
* Node.js
* PostgreSQL
* Git

### Clone Repository

```bash
git clone <https://github.com/vani020/AIOpsGuard.git>
cd AIOpsGuard
```

### Backend

```bash
cd backend
python -m venv venv
```

Activate the virtual environment:

**Windows**

```bash
venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the backend:

```bash
uvicorn main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Environment Variables

Create a `.env` file and configure the required database and LLM credentials.

```env
DATABASE_URL=your_postgresql_connection_string
LLM_API_KEY=your_api_key
```

---

## 🔐 Human-in-the-Loop Safety

AIOpsGuard follows a human-in-the-loop approach for critical actions.

```text
Incident Detected
       ↓
Recommendation Generated
       ↓
Administrator Review
       ↓
   ┌───┴───┐
   ↓       ↓
Approve   Reject
   ↓
Controlled Action
```

This ensures that critical remediation actions are reviewed before execution.

---

## 🎯 Objectives

* Continuous infrastructure monitoring
* Detection of abnormal system behavior
* Prediction of potential failures
* LLM-based root cause analysis
* Intelligent incident explanation
* Historical incident retrieval
* Context-aware recommendations
* Controlled resource/process management
* Human approval for critical actions
* Faster troubleshooting and reduced downtime

---

## 🔮 Future Scope

* Kubernetes monitoring
* Cloud infrastructure monitoring
* Distributed system monitoring
* Multi-server monitoring
* Advanced predictive models
* Automated incident prioritization
* Notification integrations
* Advanced log intelligence
* Configurable automated remediation

---

## 👥 Team

### LogicLoop

* **Vani Tyagi** — Team Lead
* **Rashika Jain** — Team Member
* **Ira Srivastava** — Team Member
* **Suhani** — Team Member

---

## ⭐ Project Vision

> **From reactive monitoring to proactive incident management.**

AIOpsGuard aims to answer not only **"Is something wrong?"**, but also:

**"What went wrong, why did it happen, could it become critical, and what should we do about it?"**
