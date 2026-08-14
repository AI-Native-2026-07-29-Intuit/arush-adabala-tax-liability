package com.uptimecrew.tax_liability.config;

import io.swagger.v3.oas.models.Components;
import io.swagger.v3.oas.models.OpenAPI;
import io.swagger.v3.oas.models.info.Info;
import io.swagger.v3.oas.models.security.SecurityRequirement;
import io.swagger.v3.oas.models.security.SecurityScheme;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Publishes the {@code /v1/taxpayers} contract as a real OpenAPI 3.1 document at {@code
 * /v3/api-docs}, declaring a Bearer/JWT {@link SecurityScheme} so Swagger UI can send the same
 * token every {@code /api/v1/**} route requires (see {@link
 * com.uptimecrew.tax_liability.security.SecurityConfig}).
 */
@Configuration
public class OpenApiConfig {

    @Bean
    public OpenAPI taxcalcOpenApi() {
        final String schemeName = "bearer-jwt";
        return new OpenAPI()
                .info(new Info()
                        .title("Taxpayers API")
                        .version("v1.0.0")
                        .description("REST API for the Taxpayers bounded context. "
                                + "All endpoints require a Bearer JWT with the appropriate scope and role."))
                .addSecurityItem(new SecurityRequirement().addList(schemeName))
                .components(new Components()
                        .addSecuritySchemes(schemeName, new SecurityScheme()
                                .type(SecurityScheme.Type.HTTP)
                                .scheme("bearer")
                                .bearerFormat("JWT")));
    }
}
