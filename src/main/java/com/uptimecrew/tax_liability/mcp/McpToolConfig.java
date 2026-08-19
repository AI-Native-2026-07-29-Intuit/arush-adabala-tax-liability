package com.uptimecrew.tax_liability.mcp;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.ai.tool.ToolCallbackProvider;
import org.springframework.ai.tool.method.MethodToolCallbackProvider;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.event.EventListener;

/**
 * Registers {@link TaxpayerMcpServer}'s {@code @Tool}-annotated methods with Spring AI's MCP
 * server (W3 D3). Unlike {@code ChatClient} function-calling, the MCP server does not
 * auto-discover {@code @Tool} methods on arbitrary beans by component-scanning alone - each
 * tool-bearing bean must be exposed explicitly via a {@link ToolCallbackProvider}, or its tools
 * never appear in {@code tools/list} and calling them fails with "Tool not found".
 */
@Configuration
public class McpToolConfig {

    private static final Logger LOG = LoggerFactory.getLogger(McpToolConfig.class);

    @Bean
    public ToolCallbackProvider taxpayerTools(TaxpayerMcpServer taxpayerMcpServer) {
        return MethodToolCallbackProvider.builder().toolObjects(taxpayerMcpServer).build();
    }

    /**
     * Spring AI's MCP auto-configuration never logs a literal "MCP server started" line (it logs
     * "Registered tools: N" instead) - this gives smoke-tests and on-call a stable, grep-able
     * confirmation that doesn't depend on a third-party library's exact wording.
     */
    @EventListener(ApplicationReadyEvent.class)
    public void logMcpServerStarted(ApplicationReadyEvent event) {
        ToolCallbackProvider tools = event.getApplicationContext().getBean(ToolCallbackProvider.class);
        LOG.info("MCP server started, tools registered: {}", tools.getToolCallbacks().length);
    }
}
