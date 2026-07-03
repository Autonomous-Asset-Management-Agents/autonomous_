/*
 * Copyright 2026 Andreas Apeldorn, Georg Apeldorn / Autonomous Asset Management Agents UG
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/**
 * Legal page drafts — Imprint (§ 5 TMG), Privacy (GDPR/DSGVO Art. 13/14),
 * Risk Disclosure.
 *
 * These are launch-ready DRAFTS based on publicly available AAAgents facts
 * (entity, contact, processors, scope). They have NOT been reviewed by
 * counsel. Every field that must be verified by a lawyer or filled in
 * once the GmbH is registered is marked [to be confirmed]. Replace before
 * any production / live-trading deploy — BaFin, DSGVO and § 18 MStV
 * place real liability on the wording here.
 *
 * See docs/6_runbooks/OPERATIONS_REFERENCE.md for the "publish → monitor"
 * sign-off flow.
 */
import { useNavigate } from "react-router-dom";
import "@/styles/landing-b.css";

interface LegalPageProps {
    kind: "imprint" | "privacy" | "risk-disclosure" | "notice";
}

const LAST_UPDATED = "2026-06-09";

const CONTENT: Record<LegalPageProps["kind"], { title: string; body: string }> = {
    imprint: {
        title: "Imprint",
        body: `========================================================================
STATUTORY IMPRINT & LEGAL DISCLOSURES (GERMAN IMPRESSUM)
========================================================================
Required pursuant to § 5 of the German Digital Services Act
(Digital-Dienste-Gesetz - DDG) and § 18 of the German Interstate Media
Treaty (Medienstaatsvertrag - MStV).

1. SERVICE PROVIDER INFORMATION
------------------------------------------------------------------------
Company Name:        Autonomous Asset Management Agents UG (haftungsbeschränkt)
Registered Office:   Wormser Strasse 5a, 67593 Westhofen, Germany
Commercial Register: Registered with the Local Court (Amtsgericht) of
                     Mainz under HRB 54409
Managing Director:   Georg Apeldorn
Corporate Contact:   Email: info@aaagents.de | Web: https://aaagents.de
Value Added Tax ID:  VAT Identification Number (USt-IdNr.) pursuant to
                     § 27a UStG: [Pending Allocation]

2. PERSON RESPONSIBLE FOR EDITORIAL CONTENT
------------------------------------------------------------------------
Pursuant to § 18 para. 2 MStV, the individual responsible for the
editorial and journalistic content of the web presences listed above is:

Georg Apeldorn
c/o Autonomous Asset Management Agents UG (haftungsbeschränkt)
Wormser Strasse 5a, 67593 Westhofen, Germany

3. LIMITATION OF LIABILITY FOR EXTERNAL HYPERLINKS
------------------------------------------------------------------------
Our web platform and documentation contain direct and indirect hyperlinks
to external third-party websites (including but not limited to GitHub,
LinkedIn, Alpaca, or external market data suppliers). Autonomous Asset
Management Agents UG has absolutely no influence over the current or future
design, content, or authorship of these linked environments. We hereby
expressly distance ourselves from any content found on linked pages that
was modified after the hyperlink was established. The respective provider
or operational manager of the linked website remains exclusively liable
for any illegal, erroneous, or incomplete contents, as well as for any
financial or operational damages arising from the use or non-use of such
information.
========================================================================`,
    },
    privacy: {
        title: "Privacy Policy",
        body: `========================================================================
COMPREHENSIVE PRIVACY POLICY (GDPR / DSGVO COMPLIANT)
========================================================================
This Privacy Policy explains how Autonomous Asset Management Agents UG
(haftungsbeschränkt) processes personal data in strict compliance with the
European General Data Protection Regulation (GDPR / DSGVO) and the German
Telecommunications-Telemedia Data Protection Act (TDDDG).

1. DATA CONTROLLER (ART. 4 NO. 7 GDPR)
------------------------------------------------------------------------
The data controller responsible for the operation of the corporate
websites and core open-source distribution channels is:

Autonomous Asset Management Agents UG (haftungsbeschränkt)
Wormser Strasse 5a, 67593 Westhofen, Germany
Email: privacy@aaagents.de

2. PROCESSING SCOPE WITHIN THE DECENTRALIZED SOFTWARE ARCHITECTURE
------------------------------------------------------------------------
We am exclusively a software development entity. We do not operate,
host, or orchestrate any centralized software-as-a-service (SaaS)
architecture, web-facing trading dashboard, or centralized user database
for the general public. The AAAgents Community Edition is compiled,
executed, and run entirely within your independent, decentralized
infrastructure, on your own account.

Consequently, the UG does not collect, track, transmit, receive, or process
any personal data, trading logs, private API credentials, wallet addresses,
or financial records resulting from your deployment of the open-source
software. For any personal data processing occurring within your
self-hosted instance, you are the exclusive and sole Data Controller
under Art. 4 No. 7 GDPR.

3. DATA PROCESSING PROTOCOLS ON OUR WEBSITE
------------------------------------------------------------------------
When visiting our official websites (https://aaagents.de or
http://autonomous-trading.de), data processing is strictly limited to
the following technical parameters:

(a) Automated Technical Server Logs
- Data Categories Processed: IP address of the accessing device, date and
  exact time of access, time zone offset, specific page/file requested,
  HTTP status/response code, volume of data transmitted, referrer website,
  browser type, operating system layout, and language settings.
- Purpose & Legal Basis: The technical processing is required to guarantee
  secure server operations, technical stability, and to defend against
  malicious cyber-attacks. The legal basis is our legitimate interest
  pursuant to Art. 6 (1) lit. f GDPR.
- Storage & Erasure: Routine technical log files are automatically erased
  after a period of 14 days, unless a validated security incident requires
  an extended preservation period for investigative purposes.

(b) Tracking and Cookie Disclaimer
Our website explicitly does NOT utilize any tracking cookies, behavioral
marketing cookies, commercial analytics frameworks, or canvas
fingerprinting. No identifiers are stored on or read from your terminal
device.

4. THIRD-PARTY PLATFORMS & GITHUB INTERACTIONS
------------------------------------------------------------------------
Our web platform does not feature contact forms or email newsletter
registrations. We exclusively link to our official company profile on
LinkedIn and our public code repository on GitHub. If you interact with
our public repositories (cloning, forking, opening issues, or submitting
pull requests), the data processing terms, tracking cookies, and privacy
rules of GitHub, Inc. (or GitHub Europe) apply parallel and independently.
The UG has no control over GitHub’s internal data-mining and profiles-tracking
activities.

5. STATUTORY RIGHTS OF THE DATA SUBJECT
------------------------------------------------------------------------
Regarding your technically logged server data, you possess the following
rights under the GDPR: Right of Access (Art. 15), Right to Rectification
(Art. 16), Right to Erasure (Art. 17), Right to Restriction of Processing
(Art. 18), and Right to Data Portability (Art. 20). You also possess the
explicit Right to Object to processing based on legitimate interests
(Art. 21) and the right to lodge a formal complaint with a competent data
protection supervisory authority (Art. 77 GDPR).

For any privacy-related inquiries, please contact: privacy@aaagents.de
========================================================================`,
    },
    "risk-disclosure": {
        title: "Risk Disclosure",
        body: `========================================================================
GLOBAL CAPITAL-MARKET & REGULATORY RISK DISCLOSURE
========================================================================
IMPORTANT NOTICE: PLEASE READ THIS ENTIRE DISCLOSURE CAREFULLY BEFORE
DOWNLOADING, COMPILING, FORKING, OR OPERATING THE AAAGENTS COMMUNITY
EDITION SOFTWARE.

1. COMPLETE EXCLUSION OF FINANCIAL SERVICES AND INVESTMENT ADVICE
------------------------------------------------------------------------
Autonomous Asset Management Agents UG (haftungsbeschränkt) operates purely
and exclusively as a software development firm. The company is NOT a
financial services provider, credit institution, investment firm, or asset
manager. It does not possess, nor does it require, licensing or
authorization from the German Federal Financial Supervisory Authority
(BaFin), the European Central Bank (ECB), or any other global financial
regulatory body under the German Securities Institutions Act (WpIG), the
German Banking Act (KWG), or the German Investment Code (KAGB).

The UG does not operate centralized trading desks, does not host real-time
trading consoles for users, does not maintain custody of client fiat or
digital currencies, and does not receive, route, or execute user
transaction orders. All activities are completed locally and decentralized
by the user.

2. MANDATORY ARCHITECTURE & NON-CONFIGURED NEUTRALITY GUARANTEE
------------------------------------------------------------------------
The AAAgents Community Edition is delivered to the public as a neutral,
non-configured, pure mathematical and statistical framework. Autonomous
Asset Management Agents UG does NOT provide, hardcode, pre-set, or optimize
real-time trading strategies, signal-sharing parameters, algorithmic
indicators, or live copy-trading configurations. The software constitutes
a mechanical tool; the operational setup, financial parameters, risk
thresholds, and active logic must be defined, validated, and embedded
exclusively by the end-user.

3. AUTONOMOUS UTILITY, DECENTRALIZED EXECUTION, AND BROKER NEUTRALITY
------------------------------------------------------------------------
By downloading and operating this open-source software, you are acting
entirely at your own risk and on your own financial account. You possess
sole, unshared responsibility for:
- Sourcing, paying for, and technically securing your own local server
  infrastructure (e.g., AWS, local hardware).
- Sourcing, validating, and auditing your own financial market data
  feeds and connections.
- Establishing, funding, and supervising independent brokerage accounts
  (e.g., via Alpaca Securities LLC, or any other third-party broker chosen
  completely independently by the user without any intermediary intervention
  by the UG).
- Reviewing, compiling, testing, and debugging the raw source code in a
  sandboxed environment before connecting it to live capital.

The UG maintains no remote administrative backdoors, network control
mechanisms, data bridges, or master "kill-switches" over your self-hosted
deployments. We cannot stop, override, modify, or reverse any financial
transaction or algorithmic decision executed by your local instances.

4. EXTREME RISK OF TOTAL CAPITAL LOSS
------------------------------------------------------------------------
Algorithmic, quantitative, and autonomous trading in financial instruments
involves extreme, systemic capital risks. The global financial markets are
inherently volatile, complex, and prone to unpredictable shifts. Autonomously
executing software agents can initiate high-frequency or high-volume
transactions that may lead to immediate, substantial, and irreversible
financial losses, including the absolute and complete loss of all capital
deployed within your connected brokerage accounts. Simulated or historical
backtesting performance metrics are mathematical abstractions and never
constitute a guaranteed indicator of future real-world trading outcomes.

5. ALGORITHMIC AND MACHINE LEARNING VULNERABILITIES
------------------------------------------------------------------------
The software framework utilizes complex machine-learning structures,
statistical weight files, and multi-agent consensus algorithms. These
models are inherently constrained by the historical boundaries of their
training data. They can fail silently, enter catastrophic feedback loops,
or generate severely flawed trading signals when encountering unprecedented
market regimes, sudden liquidity evaporation, macroeconomic shocks,
broker API responses, network latencies, or structural data-feed
gaps.

6. COMPLETE EXCLUSION OF THIRD-PARTY API AND INFRASTRUCTURE LIABILITY
------------------------------------------------------------------------
The UG expressly and comprehensively excludes any and all liability for
operational disruptions, software crashes, incorrect data processing, or
financial losses caused by the failure, latency, connection termination,
or breaking changes of third-party application programming interfaces
(APIs), third-party data feeds, external code libraries, or external
broker connectivity infrastructures.

7. LEGAL QUALIFICATION OF SOFTWARE PROVISION (GERMAN GIFT LAW PRIVILEGES)
------------------------------------------------------------------------
The AAAgents Community Edition is provided to you entirely free of charge
as a non-commercial open-source contribution. Under German civil law,
this unilateral, unentgeltliche arrangement is qualified strictly as a
statutory gift (Schenkung).

In rigorous alignment with § 521 of the German Civil Code (BGB), the
contractual, pre-contractual, and tortious liability of the UG as the
provider of a free software utility is strictly confined to acts of
intent (Vorsatz) and gross negligence (grobe Fahrlässigkeit). Liability
for ordinary or simple negligence (einfache Fahrlässigkeit) is entirely
excluded. The source code is delivered on an "AS IS" basis, without
warranties, structural guarantees, or operational conditions of any kind,
either express or implied, including but not limited to warranties of
merchantability or fitness for a specific trading purpose.

8. COMPLETE EXTINGUISHMENT OF LIABILITY FOR MODIFIED FORKS
------------------------------------------------------------------------
Any technical modification, proprietary customization, compilation
alteration, or structural forking of the source code by the user or third
parties completely breaks the chain of legal causation. Consequently,
any potential residual statutory or tortious liability of Autonomous Asset
Management Agents UG is entirely and retroactively extinguished for such
modified code bases.

9. USER REGULATORY AND COMPLIANCE MANDATES
------------------------------------------------------------------------
Depending on your legal jurisdiction, corporate status, and active trading
volume, the deployment of autonomous algorithmic trading systems may
subject you to strict local regulatory registrations, reporting regimes
(e.g., European MiFID II / MiFIR data mandates), or complex capital gains
tax liabilities. You are solely and exclusively responsible for ensuring
that your automated infrastructure satisfies all applicable regional,
national, and international laws.
========================================================================`,
    },
    notice: {
        title: "Legal Notice & Attributions (NOTICE)",
        body: `========================================================================
LEGAL NOTICE & SOFTWARE ATTRIBUTIONS (NOTICE)
========================================================================
Software Product:    autonomous_trading solution
Distributed as:      AAAgents Community Edition
Current Version:     2.4.0
Official Websites:   https://aaagents.de | http://autonomous-trading.de
Copyright Holders:   Andreas Apeldorn, Georg Apeldorn
Corporate Entity:    Autonomous Asset Management Agents UG (haftungsbeschränkt)

Copyright 2026 Andreas Apeldorn, Georg Apeldorn / Autonomous Asset
Management Agents UG (haftungsbeschränkt). All rights reserved.

This software product includes code originally developed by the copyright
holders and Autonomous Asset Management Agents UG, distributed and licensed
under the terms of the Apache License, Version 2.0 (the "License"). You may
not use this software except in compliance with the License. You may obtain
a structural copy of the License within the accompanying LICENSE file or
online at: http://www.apache.org/licenses/LICENSE-2.0

1. STRICT PROTECTION OF CORPORATE DESIGNATIONS, NAMES, AND TRADEMARKS
------------------------------------------------------------------------
Your authorization to use the software under the Apache 2.0 License is
strictly confined to the source code and does NOT grant you any legal
right, title, or interest to utilize the trade names, commercial
trademarks, service marks, corporate logos, or product designations of the
copyright holders or Autonomous Asset Management Agents UG, except as
necessary for customary and reasonable descriptive use in identifying the
technical origin of the Work.

The business designations "autonomous_trading solution", "AAAgents",
"Iron Dome", "Round Table V2", and "ConsensusEngine", alongside all
accompanying visual logos and design assets, represent protected
commercial designations and proprietary names owned exclusively by the
copyright holders and/or the UG under § 12 of the German Civil Code (BGB)
and § 5 of the German Trademark Act (MarkenG).

Any unauthorized commercial deployment of these designations for third-party
products, systems, or standalone services—most notably within the
regulated fields of financial services, quantitative investment brokerage,
proprietary trading, or discretionary asset management—is strictly
prohibited without explicit, prior written authorization from the UG.
Any modified downstream variants (forks) of this repository must be
comprehensively renamed and must not be commercialized or distributed
under any of the protected designations enumerated above.

2. ABSOLUTE EXCLUSION OF PROPRIETARY FINTECH CORE COMPONENTS
------------------------------------------------------------------------
This open-source repository represents solely the standalone, fully
decentralized Community Edition.

PLEASE NOTE: The following advanced proprietary core modules, backend
cloud systems, and institutional streaming infrastructures are expressly
NOT part of this open-source package, are NOT hosted or operated by the
UG for public use, and are NOT licensed under the Apache License 2.0:
- Encrypted Firebase Authentication Layers (FirebaseAuth)
- Cloud Secret Manager Integration Systems (GCP Secret Manager)
- Cloud Database Archival Infrastructure (SenateProtocol via Cloud SQL)
- Automated Cloud Training Pipelines & Architecture (Vertex AI / Cloud Run Jobs)
- Real-time API interface connectivity to aggregated SIP market data feeds
- RTS 22 / MiFID II Automated Compliance Export Engine APIs

These proprietary modules, advanced machine-learning weights, and
cloud-native services remain under the exclusive, closed-source proprietary
license of Autonomous Asset Management Agents UG. They are designed to be
accessible exclusively via separate, independent B2B deployments under
explicit commercial licensing terms and separate enterprise SaaS agreements.
The UG does not operate as a centralized hosting provider or data distributor
for public open-source instances.

3. DOWNSTREAM OPEN-SOURCE ATTRIBUTIONS (THIRD-PARTY COMPONENTS)
------------------------------------------------------------------------
This product utilizes or interacts with third-party open-source libraries.
The copyright holders express their gratitude to the open-source community:

* pandas-ta (Licensed under the MIT License)
  Copyright (c) 2020 Kevin Johnson.
  The structural license text is maintained natively within the respective
  code sub-directories.
========================================================================`,
    },
};

export default function Legal({ kind }: LegalPageProps) {
    const navigate = useNavigate();
    const { title, body } = CONTENT[kind];
    return (
        <div className="landing-b-root">
            <nav className="lb-nav">
                <button className="lb-nav-logo" onClick={() => navigate("/")} style={{ background: "none", border: "none", cursor: "pointer" }}>
                    aaagents<span style={{ color: "#00c27a" }}>_</span>
                </button>
                <div className="lb-nav-right">
                    <button className="lb-nav-link" onClick={() => navigate("/")}>← Back to home</button>
                </div>
            </nav>
            <article className="lb-container" style={{ padding: "60px var(--lb-gutter) 120px", maxWidth: 820 }}>
                <div className="lb-eyebrow" style={{ fontFamily: "var(--lb-mono)", fontSize: 12, letterSpacing: 2.5, textTransform: "uppercase", color: "var(--lb-muted-light)", marginBottom: 16 }}>
                    Legal · Draft pending counsel review
                </div>
                <h1 style={{ fontSize: "clamp(36px, 5vw, 56px)", fontWeight: 800, lineHeight: 1, letterSpacing: "-0.02em", margin: "0 0 32px" }}>{title}</h1>
                <pre style={{ whiteSpace: "pre-wrap", fontFamily: "var(--lb-font)", fontSize: 16, lineHeight: 1.7, color: "var(--lb-fg-light)", margin: 0 }}>
                    {body}
                </pre>
            </article>
        </div>
    );
}
