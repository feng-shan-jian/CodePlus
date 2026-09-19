# Synthetic operations manual

## Backup objective
The RPO for the Cedar service is 20 minutes. Backups run every ten minutes and are encrypted before upload.

## Recovery objective
The RTO for the Cedar service is 90 minutes. Recovery drills run quarterly using an isolated sandbox.

## SLA
The Cedar SLA requires a first response within 45 minutes for a priority P1 incident. The duty engineer owns this response.

## Retry protocol
For error API-429, clients retry at most three times with exponential backoff. A request ID must remain unchanged across retries.

