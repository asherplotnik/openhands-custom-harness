---
name: bank-microservice-navigation
description: Navigate the bank's OCP and GKE microservice journeys, source and configuration repositories, service dependencies, and shared modules when diagnosing or fixing DCMG service issues. Do not use for unrelated repositories.
---

# Bank Microservice Navigation

Use this knowledge to locate relevant code and configuration and to reason about
cross-service effects. It is trusted organizational context, not proof that a
particular service currently uses a specific dependency.

Do not invent a relationship from naming alone. Report which relationships are
confirmed by configuration or code and which are only inferred. The current
CodingAgent job may inspect only its prepared repository; use unavailable
repositories as navigation context, not as a claim that they were inspected.

## Terminology

- **MS** means microservice.
- GKE distinguishes **BFF**, internal **MS**, and **ODS** services.
- OCP services are referred to generically as MS in this guide.

## Request journeys

### Journey A — OCP

```text
Client
  → WebSEAL / F5
  → Titan application gateway
      ├─ OCP MS → API Connect → ACE → Mainframe
      └─ Composer monolith → Mainframe or database
```

### Journey B — GKE

```text
Client
  → F5
  → Apigee
  → BFF
  → Internal MS
      ├─ ODS
      └─ API Connect → Mainframe
```

These are the main paths, not a guarantee that every request traverses every
component.

## Service source repositories

Source repositories for both OCP and GKE microservices are currently in the DMS
Bitbucket project:

- https://bitbucket.dev.local:8443/projects/DMS

This may change. Inspect a repository before classifying it as a service.

## OCP configuration

Non-production OCP Helm values are in:

- Browser: https://bitbucket.dev.local:8443/projects/DCDDEV/repos/dcms-helm-values-nonprod/browse

The relevant environments are `dev` and `uat`. Each environment has a global
`values.yaml`, for example:

- https://bitbucket.dev.local:8443/projects/DCDDEV/repos/dcms-helm-values-nonprod/browse/uat/values.yaml

Determine effective OCP configuration by reading both levels:

1. Start with the environment's global `values.yaml`.
2. Apply the target service's values file.
3. Service-specific values override matching global values.

## GKE configuration

GKE Helm values are in:

- https://github.com/dbank-microservices-dcmg/dcmg-helm-values-dev

Paths encode environment and namespace information. Example:

- `apps/uat-ncc/dcmg-bff-loan/bffaccountloaneligibility.yaml`
- https://github.com/dbank-microservices-dcmg/dcmg-helm-values-dev/blob/master/apps/uat-ncc/dcmg-bff-loan/bffaccountloaneligibility.yaml

Do not apply OCP directory or property conventions to GKE configuration.

## Find outbound service calls

Inspect the caller's Helm values first. Search for `baseUrl`, service URLs,
Kubernetes DNS names, and API Connect endpoints.

OCP example:

```yaml
dcms-app.deployments.a.env.name: getAccountTransactions.baseUrl
dcms-app.deployments.a.env.value: http://getaccounttransactions-a.dcms-account-uat.svc.cluster.local:8080
```

This confirms a call to the `getAccountTransactions` service. The DNS value
identifies service `getaccounttransactions-a`, namespace `dcms-account-uat`,
and port `8080`.

Source:

- https://bitbucket.dev.local:8443/projects/DCDDEV/repos/dcms-helm-values-nonprod/browse/uat/currentaccountlasttransactions/values.yaml

GKE example:

```yaml
values.deployments.a.env.name: msAccountLoanEligibility.baseUrl
values.deployments.a.env.value: http://msaccountloaneligibility.dcmg-ms-loan.svc.cluster.local:443
```

This confirms a call to `msAccountLoanEligibility`. The DNS value identifies
service `msaccountloaneligibility`, namespace `dcmg-ms-loan`, and port `443`.

API Connect calls are not always present in Helm values. If configuration does
not establish the dependency, inspect code for endpoint keys, HTTP or generated
clients, API Connect integration classes, and shared API Connect modules. Do
not conclude that a dependency is absent merely because it is not in Helm.

## GKE shared code

Parent:

- https://bitbucket.dev.local:8443/scm/dirm/dcmg-microservices-dependencies-parent.git

Important modules:

- `apiconnect-cloud`: GKE API Connect integration  
  https://bitbucket.dev.local:8443/scm/dirm/apiconnect-cloud.git
- `cache-cloud`: shared caching  
  https://bitbucket.dev.local:8443/scm/dirm/cache-cloud.git
- `filters-ods`: filters, logging, Swagger, authentication, and validation  
  https://bitbucket.dev.local:8443/scm/dirm/filters-ods.git
- `ms-mongodb-cloud`: MongoDB Atlas integration  
  https://bitbucket.dev.local:8443/scm/dirm/ms-mongodb-cloud.git
- `objecttypes`: shared objects and data types  
  https://bitbucket.dev.local:8443/scm/dirm/objecttypes.git

## OCP shared code

Parent:

- https://bitbucket.dev.local:8443/scm/dirm/microservices-dependencies-parent.git

Important modules:

- `apiconnect`: OCP API Connect integration  
  https://bitbucket.dev.local:8443/scm/dirm/apiconnect.git
- `cache`: shared caching  
  https://bitbucket.dev.local:8443/scm/dirm/cache.git
- `mscommon`: filters, logging, Swagger, authentication, and validation  
  https://bitbucket.dev.local:8443/scm/dirm/mscommon.git
- `objecttypes`: shared objects and data types  
  https://bitbucket.dev.local:8443/scm/dirm/objecttypes.git

OCP shared repositories currently maintain `spring-boot-4-master` and `master`.
Prefer `spring-boot-4-master`, but verify the branch/version used by the target
service. Use `master` when compatibility requires it.

## Investigation method

For a DCMG bug or dependency question:

1. Identify OCP or GKE and the target environment.
2. Locate the service repository in DMS.
3. Locate its platform-specific Helm values.
4. For OCP, resolve global values plus service overrides.
5. Derive outbound calls from endpoint properties and Kubernetes DNS values.
6. Inspect code when configuration does not establish the relationship.
7. Check the appropriate platform parent and shared modules for cross-service
   behavior.
8. Base fixes on files actually available in the prepared workspace.
9. In the final diagnosis, cite the configuration property, source class,
   service URL, shared dependency, or parent version that supports each claim.
