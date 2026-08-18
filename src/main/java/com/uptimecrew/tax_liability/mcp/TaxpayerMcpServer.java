package com.uptimecrew.tax_liability.mcp;

import java.util.Objects;
import java.util.Optional;

import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;
import com.uptimecrew.tax_liability.service.TaxLiabilityService;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.ai.tool.annotation.Tool;
import org.springframework.ai.tool.annotation.ToolParam;
import org.springframework.stereotype.Service;

/**
 * Exposes a single read-only tool over the Model Context Protocol (W3 D3): the {@link Tool}
 * -annotated method below is registered with Spring AI's MCP server via {@link McpToolConfig}
 * (component-scanning alone does not surface it - see that class) and served to an LLM client
 * (e.g. Claude Code) registered against this server. Deliberately narrow - one read, one id, no
 * list/search/write surface - so an LLM agent can look up a taxpayer's read-model summary
 * without gaining any ability to mutate taxcalc data through this channel.
 */
@Service
public class TaxpayerMcpServer {

    private static final Logger LOG = LoggerFactory.getLogger(TaxpayerMcpServer.class);

    private final TaxLiabilityService service;

    public TaxpayerMcpServer(TaxLiabilityService service) {
        this.service = Objects.requireNonNull(service, "service must not be null");
    }

    @Tool(description = "Look up a taxpayer by id and return its summary read model")
    public Optional<TaxpayerReadModel> lookupTaxpayer(@ToolParam(description = "The taxpayer id") String id) {
        LOG.info("mcp tool lookupTaxpayer invoked id={}", id);
        return service.findById(id);
    }
}
