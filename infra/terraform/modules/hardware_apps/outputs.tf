output "api_fqdn" {
  value = azurerm_container_app.api.ingress[0].fqdn
}

output "web_fqdn" {
  value = azurerm_container_app.web.ingress[0].fqdn
}

output "api_app_name" {
  value = azurerm_container_app.api.name
}

output "web_app_name" {
  value = azurerm_container_app.web.name
}
