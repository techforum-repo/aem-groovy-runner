package com.day.cq.wcm.api
import org.apache.sling.api.resource.Resource
interface Page {
    String getPath()
    String getName()
    String getTitle()
    Resource getContentResource()
    Iterator<Page> listChildren()
}
interface PageManager { Page getPage(String path) }
