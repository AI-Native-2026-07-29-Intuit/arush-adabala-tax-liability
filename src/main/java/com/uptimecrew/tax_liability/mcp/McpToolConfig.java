package com.uptimecrew.tax_liability.mcp;

import org.springframework.ai.tool.ToolCallbackProvider;
import org.springframework.ai.tool.method.MethodToolCallbackProvider;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Registers {@link TaxpayerMcpServer}'s {@code @Tool}-annotated methods with Spring AI's MCP
 * server (W3 D3). Unlike {@code ChatClient} function-calling, the MCP server does not
 * auto-discover {@code @Tool} methods on arbitrary beans by component-scanning alone - each
 * tool-bearing bean must be exposed explicitly via a {@link ToolCallbackProvider}, or its tools
 * never appear in {@code tools/list} and calling them fails with "Tool not found".
 */
@Configuration
public class McpToolConfig {

    @Bean
    public ToolCallbackProvider taxpayerTools(TaxpayerMcpServer taxpayerMcpServer) {
        return MethodToolCallbackProvider.builder().toolObjects(taxpayerMcpServer).build();
    }
}
