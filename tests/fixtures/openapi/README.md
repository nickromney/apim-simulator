# Microsoft Learn Petstore import fixtures

Unmodified JSON fetched on 2026-10-02 from the two sources in Microsoft's
[import and publish tutorial](https://learn.microsoft.com/en-us/azure/api-management/import-and-publish):

- `petstore3-2026-10-02.json`: [OpenAPI 3 Petstore](https://petstore3.swagger.io/api/v3/openapi.json)
- `petstore2-2026-10-02.json`: [Swagger 2 Petstore](https://petstore.swagger.io/v2/swagger.json)

The specifications retain their Swagger/Petstore attribution and Apache 2.0
license metadata. Tests read these fixtures without network access and import
every operation without filtering or modifying the specifications.
