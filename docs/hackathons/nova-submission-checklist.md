# Amazon Nova Hackathon Submission Checklist

## Must-Haves
- [ ] Project uses Amazon Nova model/service in core flow
- [ ] Backend deployed on AWS
- [ ] Public (or approved private) code repo link ready
- [ ] ~3 minute demo video with hashtag `#AmazonNova`
- [ ] Devpost text description clearly explains Nova integration

## Evidence To Show In Demo
- [ ] Real user input -> Nova-powered response path
- [ ] One end-to-end workflow (not mock screenshots)
- [ ] Mention model choice: `Nova 2 Sonic` / `Nova 2 Lite`

## AWS Proof Clip (separate)
- [ ] Show AWS Console service page (status healthy)
- [ ] Show logs for recent request/session
- [ ] Show endpoint response in terminal/browser
- [ ] Follow `docs/hackathons/aws-proof-runbook.md` capture steps

## Repo Readiness
- [ ] `README` has local run steps
- [ ] `README` has deployment steps
- [ ] `.env.example` has required AWS vars (no secrets)
- [ ] architecture diagram included in repo + Devpost media (`docs/hackathons/nova-architecture.md`)

## Judging Optimization
- Technical Implementation (60%):
- [ ] Explain architecture + failure handling + latency strategy
- [ ] Explain why model/service choices fit workload

- Impact (20%):
- [ ] Quantify expected business/community value

- Creativity (20%):
- [ ] Show what is unique vs standard chatbot UX

## Bonus
- [ ] Publish builder.aws.com blog post
- [ ] Complete feedback survey submission
