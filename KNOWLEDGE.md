# Bank Microservice Architecture and Repository Map

## Purpose

Use this document to understand:

- The two main application request journeys.
- The distinction between OCP and GKE microservices.
- Where service source code and configuration are stored.
- How to identify calls between services.
- Which shared libraries affect multiple services.

Do not assume OCP and GKE use identical naming or configuration structures.

## Terminology

In this document, **microservice** and **MS** refer generically to microservices.

### GKE service types

GKE has three relevant service types:

- **BFF** — backend-for-frontend services.
- **MS** — internal microservices.
- **ODS** — operational data services.

### OCP service types

For OCP, this document does not distinguish between different categories of
microservices. They are all referred to as microservices or MS.

## Main Request Journeys

### Journey A — OCP

```text
Client
  → WebSEAL / F5
  → Titan application gateway
      ├─ OCP microservice
      │    → API Connect
      │    → ACE
      │    → Mainframe
      │
      └─ Composer monolith
           ├─ Mainframe
           └─ Database
```

Titan is the main application gateway in this journey. From Titan, the request
can continue through either an OCP microservice or the Composer monolith.

### Journey B — GKE

```text
Client
  → F5
  → Apigee
  → BFF
  → Internal microservice
      ├─ ODS
      └─ API Connect
           → Mainframe
```

In this journey, Apigee routes the request to a BFF. The BFF calls internal
microservices, which can then call ODS services or API Connect services.

## Microservice Source Code

The source code for both GKE and OCP microservices is currently stored in the
same Bitbucket project:

- [DMS Bitbucket project](https://bitbucket.dev.local:8443/projects/DMS)

This location may change in the future. Do not assume that every repository in
this project is necessarily a microservice; inspect the repository before
classifying it.

## OCP Configuration

OCP non-production Helm configuration is stored here:

- [dcms-helm-values-nonprod](https://bitbucket.dev.local:8443/projects/DCDDEV/repos/dcms-helm-values-nonprod/browse)

The relevant environments are currently:

- `dev`
- `uat`

Each environment has a global `values.yaml` file. For example:

- [UAT global values.yaml](https://bitbucket.dev.local:8443/projects/DCDDEV/repos/dcms-helm-values-nonprod/browse/uat/values.yaml)

Individual microservices also have their own environment-specific values
files.

### Configuration precedence

For OCP configuration:

1. Start with the environment's global `values.yaml`.
2. Apply the individual microservice's values file.
3. A value defined by the microservice overrides the corresponding global
   value.

Therefore, inspect both files before determining the final effective
configuration.

## GKE Configuration

GKE Helm configuration is stored in GitHub:

- [dcmg-helm-values-dev](https://github.com/dbank-microservices-dcmg/dcmg-helm-values-dev)

GKE configuration paths include environment and namespace information.

Example:

- [bffaccountloaneligibility.yaml](https://github.com/dbank-microservices-dcmg/dcmg-helm-values-dev/blob/master/apps/uat-ncc/dcmg-bff-loan/bffaccountloaneligibility.yaml)

Do not apply the OCP directory layout or naming conventions to GKE
configuration.

## Discovering Calls Between Services

A call from one microservice to another is often represented by an environment
variable in the calling service's `values.yaml`.

Search for entries such as:

- `baseUrl`
- Service URLs
- Kubernetes service DNS names
- API Connect endpoints

### OCP example

Configuration file:

- [currentaccountlasttransactions values.yaml](https://bitbucket.dev.local:8443/projects/DCDDEV/repos/dcms-helm-values-nonprod/browse/uat/currentaccountlasttransactions/values.yaml)

```yaml
dcms-app.deployments.a.env.name: getAccountTransactions.baseUrl
dcms-app.deployments.a.env.value: http://getaccounttransactions-a.dcms-account-uat.svc.cluster.local:8080
```

This indicates that the current service calls `getAccountTransactions`.

The Kubernetes URL provides additional information:

```text
Service:   getaccounttransactions-a
Namespace: dcms-account-uat
Port:      8080
```

### GKE example

Configuration file:

- [bffaccountloaneligibility.yaml](https://github.com/dbank-microservices-dcmg/dcmg-helm-values-dev/blob/master/apps/uat-ncc/dcmg-bff-loan/bffaccountloaneligibility.yaml)

```yaml
values.deployments.a.env.name: msAccountLoanEligibility.baseUrl
values.deployments.a.env.value: http://msaccountloaneligibility.dcmg-ms-loan.svc.cluster.local:443
```

This indicates that the BFF calls `msAccountLoanEligibility`.

The Kubernetes URL provides:

```text
Service:   msaccountloaneligibility
Namespace: dcmg-ms-loan
Port:      443
```

### API Connect calls

A microservice may also call an API Connect service.

These calls are not always visible in Helm configuration. If no relevant
endpoint is found in `values.yaml`, inspect the source code for:

- API Connect client usage.
- Endpoint or base-URL configuration keys.
- Shared API Connect modules.
- HTTP clients and generated clients.
- Service-specific integration classes.

Do not conclude that a service has no downstream dependency merely because it
is absent from its Helm values.

## Shared Cross-Service Code

Significant functionality is provided by shared modules used across many
microservices. GKE and OCP use different parent projects and shared libraries.

When behavior cannot be explained by the service repository itself, inspect
the applicable parent and shared modules.

## GKE Shared Modules

### Parent project

- [dcmg-microservices-dependencies-parent](https://bitbucket.dev.local:8443/scm/dirm/dcmg-microservices-dependencies-parent.git)

### Main shared modules

- **ApiConnect-Cloud** — integration with API Connect services from GKE  
  [apiconnect-cloud](https://bitbucket.dev.local:8443/scm/dirm/apiconnect-cloud.git)

- **cache-cloud** — shared cache functionality  
  [cache-cloud](https://bitbucket.dev.local:8443/scm/dirm/cache-cloud.git)

- **filters-ODS** — major cross-service functionality, including filters,
  logging, Swagger, authentication, and validation  
  [filters-ods](https://bitbucket.dev.local:8443/scm/dirm/filters-ods.git)

- **ms-mongodb-cloud** — MongoDB Atlas integration  
  [ms-mongodb-cloud](https://bitbucket.dev.local:8443/scm/dirm/ms-mongodb-cloud.git)

- **ObjectTypes** — shared object and data types  
  [objecttypes](https://bitbucket.dev.local:8443/scm/dirm/objecttypes.git)

## OCP Shared Modules

### Parent project

- [microservices-dependencies-parent](https://bitbucket.dev.local:8443/scm/dirm/microservices-dependencies-parent.git)

### Main shared modules

- **ApiConnect** — integration with API Connect services  
  [apiconnect](https://bitbucket.dev.local:8443/scm/dirm/apiconnect.git)

- **Cache** — shared cache functionality  
  [cache](https://bitbucket.dev.local:8443/scm/dirm/cache.git)

- **MSCommon** — major cross-service functionality, including filters,
  logging, Swagger, authentication, and validation  
  [mscommon](https://bitbucket.dev.local:8443/scm/dirm/mscommon.git)

- **ObjectTypes** — shared object and data types  
  [objecttypes](https://bitbucket.dev.local:8443/scm/dirm/objecttypes.git)

### OCP branch selection

OCP shared repositories currently maintain two principal branches:

- `spring-boot-4-master`
- `master`

Prefer `spring-boot-4-master`. Use `master` only when the relevant service or
dependency still requires it.

Always verify the branch used by the target microservice before proposing or
applying a shared-library change.

## Investigation Workflow

When investigating a service or request flow:

1. Determine whether the service runs on OCP or GKE.
2. Identify the target environment, such as dev or UAT.
3. Locate the service source repository in the DMS project.
4. Locate the corresponding Helm configuration repository.
5. For OCP, combine the global environment values with the service-specific
   overrides.
6. Search configuration for outbound service URLs and `baseUrl` properties.
7. Extract service and namespace names from Kubernetes DNS addresses.
8. If a dependency is not visible in configuration, inspect the source code.
9. Inspect the appropriate OCP or GKE shared modules when behavior comes from
   common infrastructure.
10. Clearly separate confirmed evidence from inferred relationships.

When reporting a request path, include the evidence used to establish every
hop, such as:

- Configuration file and property.
- Source file and class.
- Kubernetes service URL.
- Shared module dependency.
- Parent version and branch.
